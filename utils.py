import time
import streamlit as st
from snowflake.snowpark import Session

def get_snowflake_session(connection_parameters):
    """Create and return a Snowflake session"""
    return Session.builder.configs(connection_parameters).create()

def execute_query(session, query):
    """Execute a Snowflake query and return the results as a pandas DataFrame"""
    return session.sql(query).to_pandas()

def generate_llm_response(session, prompt, model="claude-4-sonnet"):
    """Generate a response from Snowflake's LLM and return the response and token counts"""
    # Clean prompt for SQL injection prevention
    safe_prompt = prompt.replace("'", "''")
    
    # Execute the LLM query
    query = f"""SELECT snowflake.cortex.complete('{model}', '{safe_prompt}') AS CONTENT"""
    response_df = execute_query(session, query)
    response = response_df['CONTENT'][0]
    
    # Get token counts
    input_token_query = f"""SELECT snowflake.cortex.count_tokens('llama3.1-8b', '{safe_prompt}') AS TOKEN_COUNT"""
    input_token_count = execute_query(session, input_token_query)['TOKEN_COUNT'][0]
    
    output_token_query = f"""SELECT snowflake.cortex.count_tokens('llama3.1-8b', '{response.replace("'", "''")}') AS TOKEN_COUNT"""
    output_token_count = execute_query(session, output_token_query)['TOKEN_COUNT'][0]
    
    # Calculate cost (adjust as needed based on your pricing)
    cost_per_search = round((input_token_count + output_token_count) * 2.55 / 1000000 * 2.40, 4)
    
    return response, input_token_count, output_token_count, cost_per_search

def display_token_info(input_tokens, output_tokens, cost, key="token_info"):
    """Display token usage and cost information in a text area."""
    info_text = f"""
    Input tokens: {input_tokens}
    Output tokens: {output_tokens}
    Total tokens: {input_tokens + output_tokens}
    Estimated cost: ${cost:.6f}
    """
    st.text_area("Token and Cost Information",
                  value=info_text,
                  height=100,
                  key=key)

def stream_text_effect(text):
    """Create a typing effect for the given text"""
    if st.session_state.stream_data_counter == 0:
        for word in text.split(" "):
            yield word + " "
            time.sleep(0.1)
            st.session_state.stream_data_counter += 1
    else:
        st.write(text) 