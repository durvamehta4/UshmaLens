"""The four pilot cities: centres, Census 2011 headers and state language.

City-level reference values come from TAPAS (every one carries its source).
District heat-vulnerability rows (outdoor workers, multidimensional poverty,
travel time to care) come from the national HVI table supplied with USHMA.
"""

CITIES = {
    "Mumbai": {
        "state": "Maharashtra", "lang": "mr", "coastal": True,
        "centre": [72.8777, 19.076], "hvi_districts": ["MUMBAI", "MUMBAI SUBURBAN"],
        "population_2011": 12442373,
        "vuln": {"literacy": 0.8973, "children_0_6": 0.0968, "slum": 0.4184, "elderly_60plus": 0.0857,
                 "disability": 0.0270, "kutcha": 0.016, "density": 20621},
        "ac_base": 0.45,
        "source": "Census 2011 Greater Mumbai (M Corp.); C-14 City; HL-14; Sagar et al. 2016",
    },
    "Ahmedabad": {
        "state": "Gujarat", "lang": "gu", "coastal": False,
        "centre": [72.5714, 23.0225], "hvi_districts": ["AHMADABAD"],
        "population_2011": 5577940,
        "vuln": {"literacy": 0.8829, "children_0_6": 0.1113, "slum": 0.0449, "elderly_60plus": 0.0796,
                 "disability": 0.0193, "kutcha": 0.007, "density": 12017},
        "ac_base": 0.42,
        "source": "Census 2011 Ahmedabad (M Corp.); C-14 City; HL-14; Sagar et al. 2016",
    },
    "Chennai": {
        "state": "Tamil Nadu", "lang": "ta", "coastal": True,
        "centre": [80.2707, 13.0827], "hvi_districts": ["CHENNAI"],
        "population_2011": 4646732,
        "vuln": {"literacy": 0.9018, "children_0_6": 0.0988, "slum": None, "elderly_60plus": 0.0984,
                 "disability": 0.0164, "kutcha": 0.038, "density": 26609},
        "ac_base": 0.55,
        "source": "Census 2011 Chennai (M Corp.); C-14 City; HL-14; Sagar et al. 2016",
    },
    "Hyderabad": {
        "state": "Telangana", "lang": "te", "coastal": False,
        "centre": [78.4867, 17.385], "hvi_districts": ["HYDERABAD"],
        "population_2011": 6731790,
        "vuln": {"literacy": 0.8326, "children_0_6": 0.1188, "slum": None, "elderly_60plus": 0.0677,
                 "disability": 0.0280, "kutcha": 0.028, "density": 10357},
        "ac_base": 0.50,
        "source": "Census 2011 Hyderabad (M Corp.); C-14 AP Hyderabad district; HL-14; Sagar et al. 2016",
    },
}

CITY_IDS = list(CITIES)
