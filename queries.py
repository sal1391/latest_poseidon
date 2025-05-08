# Define all SQL queries used in the application

def get_supplier_list_query():
    """Return query to get list of suppliers"""
    return """
    SELECT GP_SUPPLIER_NM from SANDBOX.MCA.MARINE_SRM_SUPP_GRP_ACTIVITY 
    GROUP by GP_SUPPLIER_NM 
    ORDER BY GP_SUPPLIER_NM
    """

def get_customer_list_query():
    """Return query to get list of customer groups"""
    return """
    SELECT CUSTOMER_GROUP_NAME from SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY 
    GROUP by CUSTOMER_GROUP_NAME 
    ORDER BY CUSTOMER_GROUP_NAME
    """

def get_supplier_group_details_query(company_name):
    """Return query to get supplier group details"""
    safe_company_name = company_name.replace("'", "''")
    return f"""
    SELECT  
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2023 GP",
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2024 GP",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN FIXED_TONS ELSE 0 END) AS "2023 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN FIXED_TONS ELSE 0 END) AS "2024 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2023 # of Transactions",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2024 # of Transactions"
    FROM 
        SANDBOX.MCA.MARINE_SRM_SUPP_GRP_ACTIVITY
    WHERE 
        GP_SUPPLIER_NM= '{safe_company_name}'
    GROUP BY 
        GP_SUPPLIER_NM
    """

def get_customer_group_details_query(company_name):
    """Return query to get customer group details"""
    safe_company_name = company_name.replace("'", "''")
    return f"""
    SELECT 
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2023 GP",
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2024 GP",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN FIXED_TONS ELSE 0 END) AS "2023 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN FIXED_TONS ELSE 0 END) AS "2024 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2023 # of Transactions",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2024 # of Transactions",
        CONCAT(
            TO_CHAR(
                ROUND(
                    SUM(CASE WHEN LIFT_YEAR = 2023 THEN FIXED_TONS ELSE 0 END) /
                    NULLIF(SUM(CASE WHEN LIFT_YEAR = 2023 THEN PRESENTED_TONS ELSE 0 END), 0) * 100, 2
                ), 'FM999.00'
            ), '%'
        ) AS "2023 Vol Success Rate",
        CONCAT(
            TO_CHAR(
                ROUND(
                    SUM(CASE WHEN LIFT_YEAR = 2024 THEN FIXED_TONS ELSE 0 END) /
                    NULLIF(SUM(CASE WHEN LIFT_YEAR = 2024 THEN PRESENTED_TONS ELSE 0 END), 0) * 100, 2
                ), 'FM999.00'
            ), '%'
        ) AS "2024 Vol Success Rate"
    FROM 
        SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY
    WHERE 
        CUSTOMER_GROUP_NAME = '{safe_company_name}'   
    GROUP BY 
        CUSTOMER_GROUP_NAME
    """

def get_customer_account_details_query(company_name):
    """Return query to get customer account details"""
    safe_company_name = company_name.replace("'", "''")
    return f"""
    SELECT
        CUSTOMER_NAME AS Account,
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2023 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2023 GP",
        CONCAT('$', TO_CHAR(ROUND(SUM(CASE WHEN LIFT_YEAR = 2024 THEN TOTAL_GP ELSE 0 END), 0), 'FM999,999,999')) AS "2024 GP",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN FIXED_TONS ELSE 0 END) AS "2023 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN FIXED_TONS ELSE 0 END) AS "2024 Mtons",
        SUM(CASE WHEN LIFT_YEAR = 2023 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2023 # of Transactions",
        SUM(CASE WHEN LIFT_YEAR = 2024 THEN NUMBER_OF_TRANSACTIONS ELSE 0 END) AS "2024 # of Transactions",
        CONCAT(
            TO_CHAR(
                ROUND(
                    SUM(CASE WHEN LIFT_YEAR = 2023 THEN FIXED_TONS ELSE 0 END) /
                    NULLIF(SUM(CASE WHEN LIFT_YEAR = 2023 THEN PRESENTED_TONS ELSE 0 END), 0) * 100, 2
                ), 'FM999.00'
            ), '%'
        ) AS "2023 Vol Success Rate",
        CONCAT(
            TO_CHAR(
                ROUND(
                    SUM(CASE WHEN LIFT_YEAR = 2024 THEN FIXED_TONS ELSE 0 END) /
                    NULLIF(SUM(CASE WHEN LIFT_YEAR = 2024 THEN PRESENTED_TONS ELSE 0 END), 0) * 100, 2
                ), 'FM999.00'
            ), '%'
        ) AS "2024 Vol Success Rate"
    FROM
        SANDBOX.MCA.MARINE_CRM_CUST_GRP_ACTIVITY
    WHERE
        CUSTOMER_GROUP_NAME = '{safe_company_name}'
    GROUP BY
        CUSTOMER_NAME, PRIMARY_BRKR
    """ 