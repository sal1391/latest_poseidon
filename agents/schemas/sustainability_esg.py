"""
Perplexity JSON Schema for Sustainability & ESG Report.
Source: agent1.ipynb cell 13
"""
from datetime import datetime


def get_schema(company_name):
    """Return the Perplexity response_format dict for SustainabilityESGReport."""
    return {
    "type": "json_schema",
    "json_schema": {
        "name": "SustainabilityESGReport",
        "schema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "SustainabilityESGReport",
            "type": "object",
            "additionalProperties": False,
            "required": [
                "company_name",
                "report_as_date",
                "org",
                "sustainability_esg",
                "notes",
                "sources"
            ],
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "The name of the company being analyzed"
                },
                "report_as_date": {
                    "type": "string",
                    "format": "date",
                    "description": "The date the report is generated or the 'as of' date"
                },
                "org": {
                    "type": "string",
                    "const": "Maritime"
                },
                "sustainability_esg": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "carbon_and_sustainability_goals",
                        "executive_leadership_and_governance",
                        "renewable_energy_and_operational_emissions",
                        "alternative_fuels_strategy_and_context",
                        "supply_chain_and_scope_3_management",
                        "corporate_initiatives_and_spillover",
                        "maritime_strategic_opportunities",
                        "regulatory_and_market_drivers"
                    ],
                    "properties": {
                        "carbon_and_sustainability_goals": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Corporate-wide carbon and sustainability goals framework",
                            "required": [
                                "commitments",
                                "evidence_summary",
                                "science_based_targets",
                                "emissions_baseline_and_progress"
                            ],
                            "properties": {
                                "commitments": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "string"
                                    }
                                },
                                "evidence_summary": {
                                    "type": "string"
                                },
                                "science_based_targets": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "sbti_status",
                                        "scope_coverage",
                                        "target_year",
                                        "target_description"
                                    ],
                                    "properties": {
                                        "sbti_status": {
                                            "type": "string",
                                            "enum": [
                                                "Validated (near-term)",
                                                "Validated (long-term/net-zero)",
                                                "Committed (validation pending)",
                                                "Not submitted",
                                                "Data not available"
                                            ]
                                        },
                                        "scope_coverage": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "string",
                                                "enum": [
                                                    "Scope 1",
                                                    "Scope 2",
                                                    "Scope 3"
                                                ]
                                            }
                                        },
                                        "target_year": {
                                            "type": "integer",
                                            "minimum": 2020,
                                            "maximum": 2050
                                        },
                                        "target_description": {
                                            "type": "string",
                                            "minLength": 20
                                        },
                                        "assurance_level": {
                                            "type": "string",
                                            "enum": [
                                                "reasonable",
                                                "limited",
                                                "none",
                                                "Data not available"
                                            ]
                                        },
                                        "base_year": {
                                            "type": "integer",
                                            "minimum": 2000,
                                            "maximum": 2050
                                        }
                                    }
                                },
                                "emissions_baseline_and_progress": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "base_year",
                                        "baseline_emissions_tco2e",
                                        "latest_year",
                                        "latest_emissions_tco2e"
                                    ],
                                    "properties": {
                                        "base_year": {
                                            "type": "integer",
                                            "minimum": 2000,
                                            "maximum": 2050
                                        },
                                        "baseline_emissions_tco2e": {
                                            "type": "number",
                                            "minimum": 0
                                        },
                                        "latest_year": {
                                            "type": "integer",
                                            "minimum": 2000,
                                            "maximum": 2100
                                        },
                                        "latest_emissions_tco2e": {
                                            "type": "number",
                                            "minimum": 0
                                        },
                                        "percent_reduction_vs_base": {
                                            "type": "number",
                                            "minimum": -100,
                                            "maximum": 100
                                        },
                                        "renewable_electricity_share_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "notes": {
                                            "type": "string"
                                        }
                                    }
                                }
                            }
                        },
                        "executive_leadership_and_governance": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Executive leadership and public commitment to environmental sustainability",
                            "required": [
                                "cso_and_governance",
                                "executive_statements"
                            ],
                            "properties": {
                                "cso_and_governance": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "cso_present",
                                        "governance_structure"
                                    ],
                                    "properties": {
                                        "cso_present": {
                                            "type": "boolean"
                                        },
                                        "cso_name": {
                                            "type": "string"
                                        },
                                        "cso_reports_to": {
                                            "type": "string",
                                            "description": "e.g., CEO, CFO, COO, Board"
                                        },
                                        "governance_structure": {
                                            "type": "string",
                                            "enum": [
                                                "Board Sustainability Committee",
                                                "Executive ESG Council",
                                                "Combined Audit & Sustainability Committee",
                                                "Management-only governance",
                                                "Data not available"
                                            ]
                                        },
                                        "incentive_link_to_esg": {
                                            "type": "string",
                                            "enum": [
                                                "Yes (executive pay)",
                                                "Yes (management)",
                                                "No",
                                                "Data not available"
                                            ]
                                        }
                                    }
                                },
                                "executive_statements": {
                                    "type": "array",
                                    "description": "CEO and board-level environmental messaging and public stance",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "speaker",
                                            "role",
                                            "venue_or_medium",
                                            "date",
                                            "stance_summary"
                                        ],
                                        "properties": {
                                            "speaker": {
                                                "type": "string"
                                            },
                                            "role": {
                                                "type": "string"
                                            },
                                            "venue_or_medium": {
                                                "type": "string",
                                                "description": "e.g., Annual report, Earnings call, Press release, Website, Conference"
                                            },
                                            "date": {
                                                "type": "string"
                                            },
                                            "stance_summary": {
                                                "type": "string",
                                                "minLength": 20,
                                                "description": "1 paragraph summarizing the leader's environmental stance and key points. No quotes."
                                            },
                                            "tone": {
                                                "type": "string",
                                                "enum": [
                                                    "ambitious",
                                                    "cautious",
                                                    "neutral",
                                                    "reactive"
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "renewable_energy_and_operational_emissions": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Renewable energy transition and operational emissions reduction strategy",
                            "required": [
                                "facility_decarbonization",
                                "business_travel_and_fleet"
                            ],
                            "properties": {
                                "facility_decarbonization": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "renewable_electricity_share_percent",
                                        "onsite_generation_present"
                                    ],
                                    "properties": {
                                        "renewable_electricity_share_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "ppas_or_green_tariffs": {
                                            "type": "boolean"
                                        },
                                        "onsite_generation_present": {
                                            "type": "boolean"
                                        },
                                        "onsite_generation_capacity_kw": {
                                            "type": "number",
                                            "minimum": 0
                                        },
                                        "energy_efficiency_measures": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                },
                                "business_travel_and_fleet": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "description": "Business travel emissions (kept distinct from Scope 3 supply chain) and fleet management",
                                    "required": [
                                        "air_travel_emissions_tco2e",
                                        "policy_controls_present"
                                    ],
                                    "properties": {
                                        "air_travel_emissions_tco2e": {
                                            "type": "number",
                                            "minimum": 0
                                        },
                                        "policy_controls_present": {
                                            "type": "boolean"
                                        },
                                        "reporting_coverage": {
                                            "type": "string",
                                            "enum": [
                                                "global",
                                                "regional",
                                                "limited",
                                                "Data not available"
                                            ]
                                        },
                                        "fleet_fuels": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "enum": [
                                                    "gasoline",
                                                    "diesel",
                                                    "hybrid",
                                                    "plug-in hybrid",
                                                    "BEV",
                                                    "hydrogen",
                                                    "other"
                                                ]
                                            }
                                        },
                                        "fleet_ev_share_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        }
                                    }
                                }
                            }
                        },
                        "alternative_fuels_strategy_and_context": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Alternative Marine Fuels context, adoption, and strategic positioning opportunity",
                            "required": [
                                "maritime_specificity",
                                "repositioning_support",
                                "alternative_fuels_technology_and_regulation",
                                "sustainability_mandates_driving_shipping"
                            ],
                            "properties": {
                                "maritime_specificity": {
                                    "type": "string",
                                    "enum": [
                                        "shipping-specific",
                                        "corporate-general",
                                        "Data not available"
                                    ]
                                },
                                "details": {
                                    "type": "string"
                                },
                                "repositioning_support": {
                                    "type": "string"
                                },
                                "alternative_fuels_technology_and_regulation": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "recognized_pathways",
                                        "regulatory_references"
                                    ],
                                    "properties": {
                                        "recognized_pathways": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "string",
                                                "enum": [
                                                    "Marine Biofuel",
                                                    "LNG",
                                                    "Methanol",
                                                    "Ammonia",
                                                    "Hydrogen",
                                                    "Other"
                                                ]
                                            }
                                        },
                                        "blend_limits_max_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "book_and_claim_participation": {
                                            "type": "string",
                                            "enum": [
                                                "yes",
                                                "no",
                                                "pilot only",
                                                "Data not available"
                                            ]
                                        },
                                        "regulatory_references": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": [
                                                    "jurisdiction",
                                                    "program"
                                                ],
                                                "properties": {
                                                    "jurisdiction": {
                                                        "type": "string",
                                                        "description": "e.g., IMO, EU, US, UK"
                                                    },
                                                    "program": {
                                                        "type": "string",
                                                        "description": "e.g., IMO 2020/2030, CII, EEXI, EU ETS, FuelEU Maritime"
                                                    },
                                                    "effective_date": {
                                                        "type": "string"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                },
                                "sustainability_mandates_driving_shipping": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "net_zero_year",
                                        "internal_carbon_price_present",
                                        "shipping_implications"
                                    ],
                                    "properties": {
                                        "net_zero_year": {
                                            "type": "integer",
                                            "minimum": 2030,
                                            "maximum": 2050
                                        },
                                        "interim_targets": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        },
                                        "internal_carbon_price_present": {
                                            "type": "boolean"
                                        },
                                        "procurement_policy_allows_book_and_claim": {
                                            "type": "boolean"
                                        },
                                        "shipping_implications": {
                                            "type": "string",
                                            "minLength": 20
                                        }
                                    }
                                }
                            }
                        },
                        "supply_chain_and_scope_3_management": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Supply chain and Scope 3 emissions management strategy",
                            "required": [
                                "purchased_goods_and_services",
                                "industry_supply_chain_considerations"
                            ],
                            "properties": {
                                "purchased_goods_and_services": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "methodology",
                                        "coverage_percent",
                                        "supplier_engagement_target_present"
                                    ],
                                    "properties": {
                                        "methodology": {
                                            "type": "string",
                                            "enum": [
                                                "GHG Protocol (spend-based)",
                                                "GHG Protocol (activity-based)",
                                                "Hybrid",
                                                "Other"
                                            ]
                                        },
                                        "coverage_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "supplier_engagement_target_present": {
                                            "type": "boolean"
                                        },
                                        "data_quality": {
                                            "type": "string",
                                            "enum": [
                                                "high",
                                                "medium",
                                                "low",
                                                "Data not available"
                                            ]
                                        },
                                        "category_breakout": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                },
                                "industry_supply_chain_considerations": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "industry",
                                        "material_topics"
                                    ],
                                    "properties": {
                                        "industry": {
                                            "type": "string"
                                        },
                                        "material_topics": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "string",
                                                "description": "e.g., metals, chemicals, packaging, logistics"
                                            }
                                        },
                                        "logistics_transport_emissions_salient": {
                                            "type": "boolean"
                                        }
                                    }
                                }
                            }
                        },
                        "corporate_initiatives_and_spillover": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Corporate sustainability initiatives and spillover potential for maritime / commercial shipping operations",
                            "required": [
                                "environmental_stewardship",
                                "waste_management_and_circularity",
                                "other_key_initiatives"
                            ],
                            "properties": {
                                "environmental_stewardship": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "water_strategy_present",
                                        "biodiversity_commitments_present"
                                    ],
                                    "properties": {
                                        "water_strategy_present": {
                                            "type": "boolean"
                                        },
                                        "biodiversity_commitments_present": {
                                            "type": "boolean"
                                        },
                                        "key_initiatives": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        },
                                        "spillover_potential_for_shipping": {
                                            "type": "string",
                                            "minLength": 10
                                        }
                                    }
                                },
                                "waste_management_and_circularity": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "zero_waste_target_present",
                                        "recycling_rate_percent"
                                    ],
                                    "properties": {
                                        "zero_waste_target_present": {
                                            "type": "boolean"
                                        },
                                        "recycling_rate_percent": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "maritime_waste_program_present": {
                                            "type": "boolean"
                                        },
                                        "notes": {
                                            "type": "string"
                                        }
                                    }
                                },
                                "other_key_initiatives": {
                                    "type": "array",
                                    "description": "General catch-all for other corporate-wide sustainability initiatives",
                                    "minItems": 1,
                                    "items": {
                                        "type": "string"
                                    }
                                }
                            }
                        },
                        "maritime_strategic_opportunities": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Strategic opportunities for fleet operations department alignment with corporate mandates",
                            "required": [
                                "gap_analysis",
                                "repositioning_and_business_case",
                                "implementation_framework"
                            ],
                            "properties": {
                                "gap_analysis": {
                                    "type": "array",
                                    "description": "Gap analysis between current corporate commitments and fleet operations",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "gap",
                                            "impact",
                                            "priority"
                                        ],
                                        "properties": {
                                            "gap": {
                                                "type": "string"
                                            },
                                            "evidence": {
                                                "type": "string"
                                            },
                                            "impact": {
                                                "type": "string",
                                                "enum": [
                                                    "high",
                                                    "medium",
                                                    "low"
                                                ]
                                            },
                                            "priority": {
                                                "type": "integer",
                                                "minimum": 1,
                                                "maximum": 5
                                            },
                                            "owner": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                },
                                "repositioning_and_business_case": {
                                    "type": "array",
                                    "description": "Potential repositioning and business case development options",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "option",
                                            "value_drivers",
                                            "success_metrics"
                                        ],
                                        "properties": {
                                            "option": {
                                                "type": "string",
                                                "description": "e.g., Alternative fuels, EU ETS compliance, verified carbon credits/offsets, operational optimization, fleet refresh"
                                            },
                                            "value_drivers": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            },
                                            "capex_estimate_usd": {
                                                "type": "number",
                                                "minimum": 0
                                            },
                                            "opex_change_usd_per_year": {
                                                "type": "number"
                                            },
                                            "success_metrics": {
                                                "type": "array",
                                                "items": {
                                                    "type": "string"
                                                }
                                            }
                                        }
                                    }
                                },
                                "implementation_framework": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "workstreams",
                                        "timeline_start",
                                        "timeline_end"
                                    ],
                                    "properties": {
                                        "workstreams": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": [
                                                    "name",
                                                    "owner_function",
                                                    "milestones"
                                                ],
                                                "properties": {
                                                    "name": {
                                                        "type": "string"
                                                    },
                                                    "owner_function": {
                                                        "type": "string"
                                                    },
                                                    "milestones": {
                                                        "type": "array",
                                                        "minItems": 1,
                                                        "items": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "milestone",
                                                                "due_date"
                                                            ],
                                                            "properties": {
                                                                "milestone": {
                                                                    "type": "string"
                                                                },
                                                                "due_date": {
                                                                    "type": "string",
                                                                    "format": "date"
                                                                },
                                                                "kpi": {
                                                                    "type": "string"
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        },
                                        "timeline_start": {
                                            "type": "string",
                                            "format": "date"
                                        },
                                        "timeline_end": {
                                            "type": "string",
                                            "format": "date"
                                        }
                                    }
                                }
                            }
                        },
                        "regulatory_and_market_drivers": {
                            "type": "object",
                            "additionalProperties": False,
                            "description": "Regulatory and market drivers supporting shipping sustainability initiatives",
                            "required": [
                                "regulatory_framework",
                                "investor_expectations"
                            ],
                            "properties": {
                                "regulatory_framework": {
                                    "type": "array",
                                    "description": "Evolving regulatory framework and shipping decarbonization requirements",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "jurisdiction",
                                            "requirement",
                                            "effective_date"
                                        ],
                                        "properties": {
                                            "jurisdiction": {
                                                "type": "string"
                                            },
                                            "requirement": {
                                                "type": "string"
                                            },
                                            "effective_date": {
                                                "type": "string"
                                            },
                                            "shipping_relevance": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                },
                                "investor_expectations": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "description": "Investor and stakeholder expectations regarding shipping emissions",
                                    "required": [
                                        "frameworks",
                                        "ratings_or_assessments"
                                    ],
                                    "properties": {
                                        "frameworks": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "string",
                                                "enum": [
                                                    "CDP",
                                                    "ISSB/TCFD",
                                                    "SASB",
                                                    "GRI",
                                                    "CSRD/ESRS",
                                                    "Other"
                                                ]
                                            }
                                        },
                                        "ratings_or_assessments": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": "e.g., MSCI, Sustainalytics, SBTi status mentions"
                                            }
                                        },
                                        "shipping_specific_expectations": {
                                            "type": "string"
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "notes": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "unknowns",
                        "methodology_notes"
                    ],
                    "properties": {
                        "unknowns": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": "^Data not available$"
                            }
                        },
                        "methodology_notes": {
                            "type": "string"
                        }
                    }
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "url"
                        ],
                        "properties": {
                            "title": {
                                "type": "string"
                            },
                            "url": {
                                "type": "string"
                            },
                            "publisher": {
                                "type": "string"
                            },
                            "published_date": {
                                "type": "string"
                            }
                        }
                    }
                }
            }
        }
    }
}
