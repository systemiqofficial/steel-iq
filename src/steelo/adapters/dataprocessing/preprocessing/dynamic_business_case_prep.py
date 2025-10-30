import pandas as pd


def read_prep_data(path_to_data):
    data = pd.read_excel(path_to_data, sheet_name="Outputs for model")
    data = data[data.Side == "Input"].copy()
    data["Value"] = data.apply(
        lambda x: x["Value"] / 1000 if x["Unit"] == "kg/t product" else x["Value"], axis=1
    ).values
    data["Unit"] = data.apply(lambda x: "t/t product" if x["Unit"] == "kg/t product" else x["Unit"], axis=1).values
    data["Value"] = data.apply(
        lambda x: x["Value"] * 227.8 if "GJ" in x["Unit"] and x["Vector"] == "Electricity" else x["Value"], axis=1
    ).values
    data["Unit"] = data.apply(
        lambda x: "kWh/t product" if x["Unit"] == "GJ/t product" and x["Vector"] == "Electricity" else x["Unit"],
        axis=1,
    ).values

    return (
        data.fillna("null")
        .groupby(
            ["Business case", "Metallic charge", "Reductant", "Metric type", "Vector", "Type", "Unit"], dropna=False
        )["Value"]
        .sum()
        .reset_index(["Unit"])
        .to_dict("index")
        .copy()
    )


def main(path_to_data, path_to_output):
    output = read_prep_data(path_to_data)

    content = f"""
bom_data = {output}
"""
    with open(path_to_output, "w") as file:
        file.write(content)


if __name__ == "__main__":
    main(path_to_data="data/2024_12_13_New business cases_MVP.xlsx", path_to_output="src/steelo/domain/BOM.py")
