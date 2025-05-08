import streamlit as st
import time
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, sum as snowflake_sum, coalesce
import os
import streamlit.components.v1 as components
import json

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
session = get_snowflake_session(SNOWFLAKE_CONNECTION)

# Initialize session state variables for caching query results
# This prevents redundant queries and improves performance
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

def login_button(clientId, domain):
    """
    Create a login button that uses Auth0 for authentication.
    
    Args:
        clientId (str): Auth0 client ID
        domain (str): Auth0 domain
    
    Returns:
        dict: User information if logged in, None otherwise
    """
    # Check if user info exists in session state
    if 'user_info' not in st.session_state:
        st.session_state.user_info = None
    
    def get_auth0_login_html(clientId, domain):
        return f"""
        <div id="auth0_result" style="display: none;"></div>
        <script src="https://cdn.auth0.com/js/auth0-spa-js/2.0/auth0-spa-js.production.js"></script>
        <script>
            let auth0Client;
            
            async function createAuth0Client() {{
                auth0Client = await auth0.createAuth0Client({{
                    domain: '{domain}',
                    clientID: '{clientId}',
                    useRefreshTokens: true,
                    cacheLocation: 'localstorage'
                }});
            }}
            
            async function login() {{
                try {{
                    await auth0Client.loginWithPopup();
                    const user = await auth0Client.getUser();
                    document.getElementById('auth0_result').innerText = JSON.stringify(user);
                    window.parent.postMessage({{type: 'streamlit:auth0:login', user: user}}, '*');
                }} catch (error) {{
                    console.error('Error during login:', error);
                }}
            }}

            createAuth0Client();
        </script>
        <button onclick="login()" style="
            background-color: #4CAF50;
            border: none;
            color: white;
            padding: 15px 32px;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            font-size: 16px;
            margin: 4px 2px;
            cursor: pointer;
            border-radius: 4px;
        ">
            Login with Auth0
        </button>
        """
    
    clientId = "wscvwhAWpFNKEn9VOwA0PVNhFz6o5uEJ" 
    domain = "dev-wfs.auth0.com"   
    # Create the login component
    components.html(
        get_auth0_login_html(clientId, domain),
        height=70,
    )

    # Return the user info
    return st.session_state.user_info

# --- Main Streamlit Application ---
st.title("Poseidon :trident:")

# Authentication - Place login button in sidebar for better UX
with st.sidebar:
    st.subheader("Authentication")
    result = login_button(AUTH0_CONFIG["clientId"], AUTH0_CONFIG["domain"])

# Only show application content after successful authentication
if result:
    # Temporarily display success message in sidebar
    success_message = st.sidebar.empty()
    success_message.success("Login success")
    success_message.empty()
    
    # Display persistent user info in sidebar for session awareness
    with st.sidebar:
        st.write(f"Welcome, {result.get('name', 'User')}")
        st.write("---")
    
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
        get_details_query = get_supplier_group_details_query if is_supplier else get_customer_group_details_query
        
        # Add a placeholder selection at the top of the list
        account_list.insert(0, f"Select {'Supplier' if is_supplier else 'Account'}")
      
        # Logic for existing accounts - retrieve and analyze data
        if account_lookup == "Existing":
            company_name = st.selectbox(list_label, account_list)
          
            if company_name == f"Select {'Supplier' if is_supplier else 'Account'}":
                st.warning("Please select a Supplier or Account.")
            else:
                try:
                    # Data retrieval phase
                    with st.spinner("Running SQL query for group details..."):
                        time.sleep(2)  # Visual indication of processing           
                        grp_result_df = execute_query(session, get_details_query(company_name))
                      
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
                st.warning("Please type an Account.")
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
    


