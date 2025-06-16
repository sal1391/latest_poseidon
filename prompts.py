# Define all prompts used in the application

def get_supplier_profile_prompt(company_name):
    return f"""
Act as a senior Supply Chain Analyst with expertise in the marine fuel supply market. Your task is to analyze {company_name}, a potential fuel vendor for World Fuel Services. Gather information from reliable sources such as [list reputable sources, including industry publications, fuel price databases, and shipping industry reports]. Focus your analysis on understanding their capabilities as a supplier to World Fuel Services, evaluating their strengths, weaknesses, risks, and potential opportunities for a mutually beneficial relationship.

Step 1:
    **Vendor Overview:**
        Company Profile: Provide a brief description of {company_name}, highlighting their key business activities, geographic reach, supply chain infrastructure, and any specialized services they offer relevant to World Fuel Services' needs.
        Fuel Offerings: Describe their range of fuel types, including their capabilities to provide specific fuel blends, alternative fuels (e.g., biofuels), or low-sulfur options.
        Experience and Capabilities: Analyze their track record in supplying fuel to the marine industry, particularly within the market segments that are of interest to World Fuel Services.
        Recent Developments: Highlight any recent developments, partnerships, or expansions that impact their capacity to meet the needs of a major customer like World Fuel Services.

Step 2:
**Market Position and Competitiveness:**
        Market Share: Assess their position in the marine fuel supply market. Consider their market share in regions of importance to World Fuel Services and how they compare to other prominent suppliers.
        Competitors: Identify their main competitors and analyze their relative strengths and weaknesses. Compare {company_name}'s pricing strategies, service offerings, and sustainability initiatives to those of their competitors.

Step 3:
**Vendor Assessment and Risk Management:**
    Supply Chain Reliability: Analyze their supply chain for reliability and flexibility, assessing their ability to consistently provide the desired quantities of fuel, on time, and at a competitive price. Identify any potential bottlenecks, geopolitical vulnerabilities, or factors that could impact supply reliability.
    Quality Control: Assess their quality control measures and the effectiveness of their fuel testing and analysis procedures.
    Sustainability and Environmental Compliance: Evaluate their environmental compliance record, their commitment to sustainability, and the extent to which their operations align with World Fuel Services' sustainability goals.
 
Step 4:
    **Vendor Assets and Infrastructure:**
        Storage Facilities: Describe the location, capacity, and capabilities of their fuel storage facilities. Consider their geographic proximity to major marine hubs relevant to World Fuel Services and the types of fuel they store.
        Barge and Vessel Fleet: Analyze their fleet of barges, tankers, or other vessels used for fuel transport and bunkering. Evaluate the size, capacity, age, and maintenance status of their vessels.
        Pipelines and Infrastructure: If applicable, describe any pipelines or other infrastructure they use to transport fuel.   
 
Step 5:
    **Collaboration and Growth Potential:**
        Contract Negotiations: Analyze their approach to contract negotiations, considering pricing flexibility, contract terms, and potential for collaboration on long-term supply arrangements.
        Technology and Innovation: Explore their adoption of new technologies, such as digital platforms for fuel management, data analysis tools, or fuel-blending techniques, and how these innovations align with World Fuel Services' strategies.
        Joint Ventures and Partnerships: Assess the potential for collaborating with them on specific initiatives such as fuel blending or joint venture ventures to meet emerging fuel needs.
 
Step 5:
**Recommendation for World Fuel Services:**
Based on your analysis, provide recommendations for World Fuel Services regarding their decision to partner with {company_name}.
Consider their capabilities, strengths, risks, and growth potential, particularly in the context of their ability to provide competitive pricing, reliable supply, sustainable fuel solutions, and opportunities for collaboration.
 
Finally, please provide any insights you have about emerging trends in the fuel market that might impact {company_name} or their competitors.
"""

def get_customer_profile_prompt(company_name):
    return f"""
As the Senior Director of Fuel Marketing for World Fuel Services, I need a comprehensive maritime profile of {company_name} Maritime or {company_name} Chartering a maritime company. This profile should provide insights into their operations, sustainability goals, and potential opportunities for partnership, specifically focusing on enhancing our relationship and driving sales of our fuel and sustainability products.

Search Strategy Intructions: The target company operates in the maritime/shipping industry. When you search for {company_name}, if the initial search is unclear or leads to multiple corporate divisions (e.g., a "Cloud" vs. a "Maritime" division), your primary task is to identify and focus on the maritime-specific entity. Consider searching for variations such as {company_name} + 'Maritime', 'Shipping', 'Lines', 'Tankers', 'Charter', 'Holding' or 'Group' to ensure you identify the correct operational company.

If unable to find then intruct the user to search New Customer instead.


{company_name} is a company focused on its specific role and focus within the maritime industry. To effectively tailor our fuel and sustainability solutions to their needs, and to identify strategic opportunities for a stronger partnership, please provide a detailed profile that covers the following areas:

 ***1. ***Company Overview***
 
    *Core Business Operations*: Briefly describe their core business activities, including their role in the fuel or energy sector.
    *Key Recent Developments*: Highlight any recent developments, mergers, acquisitions, or significant changes to their operations.
    *Industry Trends*: Briefly mention any relevant industry trends or regulatory changes impacting their operations.
 
***2. ***Business Objectives***
 
    *Fuel Consumption*: Outline their current objectives and strategies related to fuel consumption, including efforts to optimize efficiency and minimize costs.
    *Energy Efficienc*y: Identify their initiatives aimed at improving energy efficiency within their operations.
    *Sustainability Efforts*: Highlight any current or planned sustainability initiatives, such as reducing carbon emissions, transitioning to cleaner energy sources, or adopting innovative fuel solutions. Identify any initiatives that could align with World Fuel Service's offerings.
 
**3. ***Fuel Consumption Strategy Alignment with Industry Trends***
 
    *Comparisons*: Identify 5 ways in which {company_name}'s fuel consumption strategy aligns with current industry trends in fuel management, efficiency, and innovation.
    *Contrasts*: Identify 5 ways in which {company_name}'s fuel consumption strategy diverges from current industry trends, highlighting potential areas for growth or improvement.

***4.Fleet Information***
 
     Intructions: When searching for informations ensure accuracy by using professional databases like VesselFinder, MarineTraffic,Seasearcher or relevant maritime industry websites and avoid double-counting vessels from multiple sources.
        *Fleet Composition*: Provide an overview of the fleet composition, including the number of vessels and their specific roles (e.g., tankers, bulk carriers, container ships). Provide the total number of vessels.Provide insight into {company_name}'s fleet management strategies and highlight any notable vessels within the fleet.
 
***5. ***Financial Insights***
 
    *Revenue & Growth Trends*: Provide recent financial data (from reputable sources), such as revenue figures and growth trends over the past [Specify timeframe]. Ensure the data is relevant to a potential partnership with World Fuel Services. Include source
    *Fuel & Sustainability Investments:* Identify any significant investments they have made in fuel-related or sustainability projects.

***6. Main Competitors***
 
    Instructions:
        Identify each of {company_name}'s direct competitors in the marine market.
        
 ***7. ***Time-Charterers***
 
    Instructions:
        Identify {company_name}'s main time-charterers.

***8. ***Sustainability Roadmap***
 
    *Sustainability Goals*: Outline their specific sustainability goals, targets, or commitments.
    *Initiatives & Policies*: Detail any existing sustainability policies, programs, or initiatives they have implemented.
    *Milestones & Future Plans*: Highlight any notable milestones achieved and future plans in relation to their sustainability roadmap.        
        
 
***9. ***Strategic Synthesis & Forward-Looking Analysis***
    *Finally, based on your complete analysis, provide a brief strategic summary:
       Emerging Trends Impact: Briefly describe one emerging trend in the fuel or maritime market (e.g., new alternative fuels, carbon trading, digitalization) that is most likely to impact {company_name} in the next 1-3 years.  
"""

def get_welcome_message():
    return """Welcome to Poseidon! Dive into the depths of data with Poseidon, your ultimate tool for navigating the seas of market intelligence.
    
Choose Your Voyage:
   
**Supplier Insight:** Explore the waters where suppliers and vendors thrive. Here, you'll uncover insights to optimize your supply chain and discover new opportunities.
   
**Customer Insight:** Navigate through the currents of customer behavior and preferences. This tab will help you understand your audience better, enhancing your marketing strategies and customer engagement.
   
**Select a Tab** to begin your journey and harness the power of Poseidon's Trident to spearhead your research efforts.
    """ 