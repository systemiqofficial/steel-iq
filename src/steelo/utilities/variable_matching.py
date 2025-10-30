from typing import Dict

# TECHNOLOGY GROUPINGS

MYPY_DICT_STR_LIST = Dict[str, list]

FURNACE_GROUP_DICT: MYPY_DICT_STR_LIST = {
    "blast_furnace": [
        "Avg BF-BOF",
        "BAT BF-BOF",
        "BAT BF-BOF_bio PCI",
        "BAT BF-BOF_H2 PCI",
        "BAT BF-BOF+CCUS",
        "BAT BF-BOF+BECCUS",
        "BAT BF-BOF+CCU",
    ],
    "dri-bof": ["DRI-Melt-BOF", "DRI-Melt-BOF_100% zero-C H2", "DRI-Melt-BOF+CCUS"],
    "dri-eaf": [
        "DRI-EAF",
        "DRI-EAF_50% bio-CH4",
        "DRI-EAF_50% green H2",
        "DRI-EAF+CCUS",
        "DRI-EAF_100% green H2",
    ],
    "smelting_reduction": ["Smelting Reduction", "Smelting Reduction+CCUS"],
    "eaf-basic": ["EAF"],
    "eaf-advanced": ["Electrolyzer-EAF", "Electrowinning-EAF"],
    "ccs": [
        "BAT BF-BOF+BECCUS",
        "BAT BF-BOF+CCUS",
        "DRI-Melt-BOF+CCUS",
        "DRI-EAF+CCUS",
        "Smelting Reduction+CCUS",
    ],
    "ccu": ["BAT BF-BOF+CCU"],
}
FURNACE_GROUP_DICT["dri"] = FURNACE_GROUP_DICT["dri-bof"] + FURNACE_GROUP_DICT["dri-eaf"]
FURNACE_GROUP_DICT["eaf-all"] = FURNACE_GROUP_DICT["eaf-basic"] + FURNACE_GROUP_DICT["eaf-advanced"]


# Land use classification labels and their corresponding numerical codes
LULC_LABELS_TO_NUM = {
    "Cropland": [10, 20],
    "Cropland Herbaceous": [11],
    "Cropland Tree/Shrub": [12],
    "Mosaic Cropland": [30],
    "Mosaic Natural Vegetation": [40],
    "Tree Cover": [50, 60, 61, 62, 70, 71, 80, 81, 90],
    "Mosaic Tree and Shrubland": [100],
    "Mosaic Herbaceous": [110],
    "Shrubland": [120, 121, 122],
    "Grassland": [130],
    "Lichens and Mosses": [140],
    "Sparse Vegetation": [150, 152, 153],
    "Shrub Cover": [180],
    "Urban": [190],
    "Bare Areas": [200, 201, 202],
    "Water": [210],
    "Snow and Ice": [220],
}

# Power mix coverage mapping
POWER_MIX_TO_COVERAGE_MAP = {
    "Grid only": 0.0,
    "Not included": 0.0,
    "85% baseload + 15% grid": 0.85,
    "95% baseload + 5% grid": 0.95,
}
