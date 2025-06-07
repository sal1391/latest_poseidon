import streamlit as st
import time
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, sum as snowflake_sum, coalesce
import os
from auth0_component import login_button
import json
import jwt  # Added import for JWT decoding

# Import custom modules for specific functionality
from prompts import get_supplier_profile_prompt, get_customer_profile_prompt, get_welcome_message
from utils import get_snowflake_session, execute_query, generate_llm_response, display_token_info, stream_text_effect
from queries import (
    get_supplier_list_query, 
    get_customer_list_query, 
    get_supplier_group_details_query, 
    get_customer_group_details_query, 
    get_customer_account_details_query
)

# Import connection parameters from config
# Auth0 and Snowflake credentials are stored in a separate file for security
try:
    from config import SNOWFLAKE_CONNECTION, AUTH0_CONFIG
except ImportError:
    st.error("""
    Could not find config.py file with SNOWFLAKE_CONNECTION and AUTH0_CONFIG.
    Please copy config_template.py to config.py and add your credentials.
    """)
    SNOWFLAKE_CONNECTION = {
        "account": "",
        "user": "",
        "password": ""
    }
    st.stop()  # Prevent app execution without proper configuration

# Create a Snowflake session
session = Session.builder.configs(SNOWFLAKE_CONNECTION).create()

# Initialize session state variables for caching query results
if 'supplier_list' not in st.session_state:
    st.session_state.supplier_list = None
if 'customer_list' not in st.session_state:
    st.session_state.customer_list = None

def get_account_list(session, is_supplier):
    """
    Retrieve and cache account lists to minimize redundant database queries.
    
    Args:
        session (Session): Active Snowflake session object
        is_supplier (bool): Flag to determine whether to fetch supplier or customer list
    
    Returns:
        list: A list of supplier or customer names
    """
    if is_supplier:
        if st.session_state.supplier_list is None:
            st.session_state.supplier_list = execute_query(session, get_supplier_list_query())['GP_SUPPLIER_NM'].tolist()
        return st.session_state.supplier_list
    else:
        if st.session_state.customer_list is None:
            st.session_state.customer_list = execute_query(session, get_customer_list_query())['CUSTOMER_GROUP_NAME'].tolist()
        return st.session_state.customer_list

def generate_company_profile(session, company_name, profile_type="customer"):
    """
    Generate an AI-powered company profile based on database information.
    
    This function retrieves relevant data and uses an LLM to generate
    a comprehensive analysis of the specified company.
    
    Args:
        session (Session): Active Snowflake session object
        company_name (str): Name of the company to generate profile for
        profile_type (str): Type of profile - either "customer" or "supplier"
    
    Returns:
        tuple: (response_text, input_tokens, output_tokens, cost)
    """
    if profile_type == "supplier":
        prompt = get_supplier_profile_prompt(company_name)
    else:
        prompt = get_customer_profile_prompt(company_name)
    
    response, input_tokens, output_tokens, cost = generate_llm_response(session, prompt)
    return response, input_tokens, output_tokens, cost

# --- Main Streamlit Application ---
st.title("Poseidon :trident:")

# Authentication - Place login button in sidebar for better UX
with st.sidebar:
    st.subheader("Authentication")
    result = login_button(AUTH0_CONFIG["clientId"], AUTH0_CONFIG["domain"])

# Only show application content after successful authentication
REQUIRED_ROLE = "Poseidon:Sales" 

if result:
    # Get the id_token or access_token from result
    token = result.get("token")
    if token:
        try:
            # Decode JWT (skip signature verification for demo; verify in prod!)
            decoded = jwt.decode(token, options={"verify_signature": False})

            # Try custom claim first, then default 'roles'
            roles = (
                decoded.get("https://wfscorp.com/custom-claims", {}).get("roles", [])
                or decoded.get("roles", [])
            )

            if REQUIRED_ROLE in roles:
                # Show login success and user info in sidebar
                with st.sidebar:
                    st.success("Login success")
                    st.write(f"Welcome, {result.get('name', 'User')}")
                    st.write("---")
                # ---- Place your protected app content below ----

                # Initialize counters for text animation effects
                if 'stream_data_counter' not in st.session_state:
                    st.session_state.stream_data_counter = 0

                # Display animated welcome message
                st.write(stream_text_effect(get_welcome_message()))

                # Create main navigation tabs for different analysis modes
                tabs = st.tabs(["**Supplier Insight**", "**Customer Insight**"])

                def render_account_tab(session, tab_type="customer"):
                    """
                    Unified function to render either supplier or customer analysis tabs.

                    This implements the DRY principle by using a single function with
                    conditional logic rather than separate functions for each tab type.

                    Args:
                        session (Session): Active Snowflake session object
                        tab_type (str): The tab type - either "customer" or "supplier"
                    """
                    is_supplier = tab_type == "supplier"
                    tab_title = "Supplier Insight" if is_supplier else "Customer Insight"
                    st.write(f"Welcome to {tab_title}!")

                    # Configure UI elements based on tab type
                    account_type_list = ["Select Account Type", "New", "Existing"]
                    key_prefix = "vendor" if is_supplier else "customer"
                    account_lookup = st.selectbox("Account Type", account_type_list, key=f"{key_prefix}_account_type")

                    # Get appropriate list based on account type
                    account_list = get_account_list(session, is_supplier)
                    list_label = "Supplier Group" if is_supplier else "Account Group"
                    
                    # This stores the appropriate function (get_supplier_group_details_query or get_customer_group_details_query)
                    get_details_query_func = get_supplier_group_details_query if is_supplier else get_customer_group_details_query

                    # Logic for existing accounts - retrieve and analyze data
                    if account_lookup == "Existing":
                        # Dynamically determine the placeholder text
                        placeholder_text = f"Select {'Supplier' if is_supplier else 'Account'}"

                        # Create a copy of the original account_list.
                        # Then, insert the placeholder ONLY ONCE at the beginning of this copy.
                        selectbox_options = account_list.copy()
                        selectbox_options.insert(0, placeholder_text)

                        company_name = st.selectbox(list_label, selectbox_options)

                        if company_name == placeholder_text:
                            # Corrected warning message to be dynamic and grammatically correct
                            st.warning(f"Please select a {'Supplier' if is_supplier else 'Account'}.")
                        else:
                            try:
                                # Data retrieval phase
                                with st.spinner("Running SQL query for group details..."):
                                    time.sleep(2)  # Visual indication of processing
                                    # Call the assigned function and pass is_supplier
                                    grp_result_df = execute_query(session, get_details_query_func(company_name))

                                    if grp_result_df.empty:
                                        st.write("No data found for the selected company name.")
                                    else:
                                        st.dataframe(grp_result_df.set_index(grp_result_df.columns[0]))

                                # AI analysis phase
                                with st.spinner("Generating AI response..."):
                                    time.sleep(2)  # Visual indication of processing
                                    profile, input_tokens, output_tokens, cost = generate_company_profile(
                                        session,
                                        company_name,
                                        profile_type=tab_type
                                    )

                                    # Display token usage metrics and AI-generated analysis
                                    display_token_info(input_tokens, output_tokens, cost)
                                    st.write("AI generated response:")
                                    st.text_area(f"Summarization of {company_name}", value=profile, height=3150)
                            except Exception as e:
                                st.error(f"We encountered an issue while processing your request. Details: {str(e)}")
                                # Log detailed error for debugging while showing user-friendly message
                                import logging
                                logging.error(f"Error processing request: {str(e)}", exc_info=True)

                    # Logic for new accounts - AI analysis only without database lookup
                    elif account_lookup == "New":
                        company_name = st.text_input("Account:")

                        if company_name == "":
                            st.warning(f"Please type a {'Supplier' if is_supplier else 'Account'}.") # Dynamic warning message
                        else:
                            try:
                                with st.spinner("Generating AI response..."):
                                    time.sleep(2)  # Visual indication of processing
                                    profile, input_tokens, output_tokens, cost = generate_company_profile(
                                        session,
                                        company_name,
                                        profile_type=tab_type
                                    )

                                    # Display token usage metrics and AI-generated analysis
                                    display_token_info(input_tokens, output_tokens, cost)
                                    st.write("AI generated response:")
                                    st.text_area(f"Summarization of {company_name}", value=profile, height=3150)
                            except Exception as e:
                                st.error(f"We encountered an issue while processing your request. Details: {str(e)}")
                                # Log detailed error for debugging while showing user-friendly message
                                import logging
                                logging.error(f"Error processing request: {str(e)}", exc_info=True)
                    else:
                        st.write("Please select a valid account type.")

                # Render the appropriate tab content
                with tabs[0]:  # Supplier Insight Tab
                    render_account_tab(session, "supplier")

                with tabs[1]:  # Customer Insight Tab
                    render_account_tab(session, "customer")
            else:
                # Security measure - don't show any application content until authenticated
                st.warning("Please log in.")
