import streamlit as st
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, sum as snowflake_sum, coalesce
import json
import time

# Get the active Snowflake session

connection_parameters = {
    "account": "wfs.us-east-1",
    "user": "csalgado@wfscorp.com",
    "password": ""
}
# Create a Snowflake session
session = Session.builder.configs(connection_parameters).create()


# Function to generate company profile
def generate_company_profile(session, company_name):
    about_company_q = f"""
As the Senior Director of Fuel Marketing for World Fuel Services, I need a comprehensive profile of {company_name}, a company operating in the marine market. This profile should provide insights into their operations, sustainability goals, and potential opportunities for partnership, specifically focusing on enhancing our relationship and driving sales of our fuel and sustainability products.
Intructions: For each task, synthesize the information you gather from multiple sources and provide insightful analysis that goes beyond simply summarizing data

{company_name} is a [Briefly describe the customer's business] operating in the marine market. To effectively tailor our fuel and sustainability solutions to their needs, and to identify strategic opportunities for a stronger partnership, please provide a detailed profile that covers the following areas:

 ***1. ***Company Overview***

    *Core Business Operations*: Briefly describe their core business activities, including their role in the fuel or energy sector.
    *Key Recent Developments*: Highlight any recent developments, mergers, acquisitions, or significant changes to their operations.
    *Industry Trends*: Briefly mention any relevant industry trends or regulatory changes impacting their operations.

 ***2. ***Business Objectives***

    *Fuel Consumption*: Outline their current objectives and strategies related to fuel consumption, including efforts to optimize efficiency and minimize costs.
    *Energy Efficienc*y: Identify their initiatives aimed at improving energy efficiency within their operations.
    *Sustainability Efforts*: Highlight any current or planned sustainability initiatives, such as reducing carbon emissions, transitioning to cleaner energy sources, or adopting innovative fuel solutions. Identify any initiatives that could align with World Fuel Service's offerings.

 ***3. ***Sustainability Roadmap***

    *Sustainability Goals*: Outline their specific sustainability goals, targets, or commitments.
    *Initiatives & Policies*: Detail any existing sustainability policies, programs, or initiatives they have implemented.
    *Milestones & Future Plans*: Highlight any notable milestones achieved and future plans in relation to their sustainability roadmap.

 ***4. ***Financial Insights***

    *Revenue & Growth Trends*: Provide recent financial data (from reputable sources), such as revenue figures and growth trends over the past [Specify timeframe]. Ensure the data is relevant to a potential partnership with World Fuel Services.
    *Fuel & Sustainability Investments:* Identify any significant investments they have made in fuel-related or sustainability projects.
    
 ***5. ***Opportunities for Partnership***

    *Fuel Products Alignment*: Identify specific areas where World Fuel Services' fuel products could benefit their operations and support their goals.
    *Sustainability Solutions*: Highlight how World Fuel Services' sustainability solutions can assist them in achieving their sustainability goals.
    *Concrete Recommendations*: Provide data-driven recommendations for strengthening our relationship, positioning World Fuel Services as a key partner in their sustainability efforts, and driving sales of our products.

 ***6. ***Fuel Consumption Strategy Alignment with Industry Trends***

    *Comparisons*: Identify 5 ways in which {company_name}'s fuel consumption strategy aligns with current industry trends in fuel management, efficiency, and innovation.
    *Contrasts*: Identify 5 ways in which {company_name}'s fuel consumption strategy diverges from current industry trends, highlighting potential areas for growth or improvement.
 ***7. ***Alignment with World Fuel Services***
    
    *Commonalities: Based on the first five topics (Company Overview, Business Objectives, Sustainability Roadmap, Financial Insights, Opportunities for Partnership), highlight 5 key commonalities between {company_name} and World Fuel Services. Emphasize shared values, priorities, or potential areas for collaboration.
    
 ***8.Fleet Information***

     Intructions: When searching for informations ensure accuracy by using professional databases like VesselFinder, MarineTraffic,Seasearcher or relevant maritime industry websites and avoid double-counting vessels from multiple sources.
        *Fleet Composition*: Provide an overview of the fleet composition, including the number of vessels and their specific roles (e.g., tankers, bulk carriers, container ships). Provide the total number of vessels.Provide insight into {company_name}’s fleet management strategies and highlight any notable vessels within the fleet.

***9. Main Competitors***

    Instructions: 
        Identify each of {company_name}'s direct competitors in the marine market. 
        
 ***10. ***Time-Charterers***

    Instructions: 
        Identify {company_name}'s main time-charterers.

Finally, please provide any insights you have about emerging trends in the fuel market that might impact {company_name} or their competitors.
                                
    """

    query = f"""SELECT snowflake.cortex.complete('llama3-70b', '{about_company_q.replace("'", "''")}') AS CONTENT"""
    about_company_ans = session.sql(query).to_pandas()['CONTENT'][0]

     # Assuming the response includes token usage information
    token_count_query = f"""SELECT snowflake.cortex.count_tokens('llama3-70b', '{about_company_q.replace("'", "''")}') AS TOKEN_COUNT"""
    token_count = session.sql(token_count_query).to_pandas()['TOKEN_COUNT'][0]
    
    return about_company_ans,token_count

# Title and description
st.title("Poseidon :trident:")
st.write("Welcome to Poseidon! Please select an Account to get started.")

account_type_list = ["Select Account Type", "New", "Existing"]
account_lookup = st.selectbox("Account Type", account_type_list)

account_list = session.sql("SELECT CUSTOMER_GROUP_NAME from SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY GROUP by CUSTOMER_GROUP_NAME").to_pandas()['CUSTOMER_GROUP_NAME'].tolist()
account_list.insert(0, "Select Account")

if account_lookup == "Existing":
    company_name = st.selectbox("Account Group", account_list)
    
    if company_name == "Select Account":
        st.warning("Please select an Account.")
    else:
        try:
            
            with st.spinner("Running SQL query for group details..."):
                time.sleep(2)            
                sql_query_grp_details = f"""
                    SELECT  PRIMARY_BRKR As "Primary Broker",
                            CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2023 GP",
                            CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2024 GP",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN QTY_MTONS ELSE 0 END), 0) AS "2023 Mtons",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN QTY_MTONS ELSE 0 END), 0) AS "2024 Mtons"
                        FROM SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY
                        WHERE CUSTOMER_GROUP_NAME = '{company_name.replace("'", "''")}'
                        GROUP BY PRIMARY_BRKR
                        """
                grp_result_df = session.sql(sql_query_grp_details).to_pandas()
                
                if grp_result_df.empty:
                    st.write("No data found for the selected company name.")
                else:
                    st.dataframe(grp_result_df.set_index(grp_result_df.columns[0]))
                
            with st.spinner("Running SQL query for account details..."):
                time.sleep(2)  
                sql_query = f"""
                    SELECT 
                            CUSTOMER_NAME AS "Account(s)",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN TOTAL_GP ELSE 0 END),0) AS "2023 GP",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN TOTAL_GP ELSE 0 END),0) AS "2024 GP",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN QTY_MTONS ELSE 0 END), 0) AS "2023 Mtons",
                            ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN QTY_MTONS ELSE 0 END), 0) AS "2024 Mtons"
                        FROM SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY
                        WHERE CUSTOMER_GROUP_NAME = '{company_name.replace("'", "''")}'
                        GROUP BY CUSTOMER_NAME, PRIMARY_BRKR
                        ORDER BY "2024 GP" DESC   
                        """
                result_df = session.sql(sql_query).to_pandas()
                    
                result_df["2023 GP"] = result_df["2023 GP"].apply(lambda x: f"${x:,.0f}")
                result_df["2024 GP"] = result_df["2024 GP"].apply(lambda x: f"${x:,.0f}")
                    
                st.dataframe(result_df.set_index(result_df.columns[0]))

            with st.spinner("Generating AI response..."):
                 time.sleep(2)
                # Calls LLM Prompt
                 profile, token_count = generate_company_profile(session, company_name)             
     

                # Token count output query
            token_count_output_query = f"""SELECT snowflake.cortex.count_tokens('llama3-70b', '{profile.replace("'", "''")}') AS OUTPUT_TOKEN_COUNT"""
            output_token_count = session.sql(token_count_output_query).to_pandas()['OUTPUT_TOKEN_COUNT'][0]

                # Calculate the cost per search
            cost_per_search = round((token_count + output_token_count)* 1.21 / 1000000 * 2.40, 4)

                # Display the AI generated response and token count
            st.text_area("Token and Cost Information",
                f"Token Count Input: {token_count}\n"
                f"Token Count Output: {output_token_count}\n"
                f"Cost per Search: ${cost_per_search:.4f}",
                height=100,
                max_chars=None,
                key="token_info")
            st.write("AI generated response:")
            st.text_area(f"Summarization of {company_name}", value=profile, height=3150)
        except Exception as e:
            st.error(f"An error occurred: {e}")

elif account_lookup == "New":
    company_name = st.text_input("Account:")
    
    if company_name == "":
        st.warning("Please type an Account.")
    else:
        try:
           with st.spinner("Generating AI response..."):
                time.sleep(2) 
                # Calls LLM Prompt
                profile, token_count = generate_company_profile(session, company_name)             
            
            
                # Token count output query
                token_count_output_query = f"""SELECT snowflake.cortex.count_tokens('llama3-70b', '{profile.replace("'", "''")}') AS OUTPUT_TOKEN_COUNT"""
                output_token_count = session.sql(token_count_output_query).to_pandas()['OUTPUT_TOKEN_COUNT'][0]
    
                # Calculate the cost per search
                cost_per_search = round((token_count + output_token_count)* 1.21 / 1000000 * 2.40, 4)
    
                # Display the AI generated response and token count
                st.text_area("Token and Cost Information",
                f"Token Count Input: {token_count}\n"
                f"Token Count Output: {output_token_count}\n"
                f"Cost per Search: ${cost_per_search:.4f}",
                height=100,
                max_chars=None,
                key="token_info")
                st.write("AI generated response:")
                st.text_area(f"Summarization of {company_name}", value=profile, height=3150)
        except Exception as e:
            st.error(f"An error occurred: {e}")
else:
    st.write("Please select a valid account type.")

