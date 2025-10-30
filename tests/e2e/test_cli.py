import pytest
import textwrap

# Import geocoder initialization functions
from steelo.adapters.dataprocessing.preprocessing.iso3_finder import (
    derive_iso3,
    Coordinate,
    reset_reverse_geocoder,
)


def create_csv_in_path(path, content):
    with open(path, "w") as f:
        f.write(textwrap.dedent(content).strip())
    return path


@pytest.fixture(autouse=True)
def initialize_geocoder():
    """Initialize the geocoder with some test coordinates for the e2e tests."""
    # Reset any existing geocoder
    reset_reverse_geocoder()

    # Create some test coordinates covering the regions used in the test data
    # Include coordinates close to the actual test data locations
    test_coordinates = [
        # Belgium - close to test plant location (50.414998, 4.532443)
        Coordinate(lat=50.414998, lon=4.532443, iso3="BEL"),  # Châtelet, Belgium
        Coordinate(lat=50.8503, lon=4.3517, iso3="BEL"),  # Brussels, Belgium
        # China - close to test plant locations (36.152418, 114.15839) and (36.122129, 114.283145)
        Coordinate(lat=36.152418, lon=114.15839, iso3="CHN"),  # Anyang, China (exact match)
        Coordinate(lat=36.122129, lon=114.283145, iso3="CHN"),  # Anyang, China (exact match)
        Coordinate(lat=36.0, lon=114.0, iso3="CHN"),  # Near Anyang
        Coordinate(lat=39.9042, lon=116.4074, iso3="CHN"),  # Beijing, China
        # Other countries for broader coverage
        Coordinate(lat=-34.6037, lon=-58.3816, iso3="ARG"),  # Buenos Aires, Argentina
        Coordinate(lat=-33.8688, lon=151.2093, iso3="AUS"),  # Sydney, Australia
        Coordinate(lat=48.2082, lon=16.3738, iso3="AUT"),  # Vienna, Austria
        Coordinate(lat=51.5074, lon=-0.1278, iso3="GBR"),  # London, UK
        Coordinate(lat=48.8566, lon=2.3522, iso3="FRA"),  # Paris, France
        Coordinate(lat=52.5200, lon=13.4050, iso3="DEU"),  # Berlin, Germany
        Coordinate(lat=40.7128, lon=-74.0060, iso3="USA"),  # New York, USA
        Coordinate(lat=35.6762, lon=139.6503, iso3="JPN"),  # Tokyo, Japan
    ]

    # Initialize the geocoder with a coordinate that's actually in our list
    derive_iso3(50.8503, 4.3517, coordinates=test_coordinates)

    yield

    # Clean up after test
    reset_reverse_geocoder()


@pytest.fixture
def steel_plant_csv(tmp_path):
    """
    Write some steel plant data to a temporary CSV file.
    """
    steel_plant_csv_content = """
        Plant ID,Plant name (English),Plant name (other language),Other plant names (English),Other plant names (other language),Owner,Owner (other language),Owner GEM ID,Owner PermID,SOE Status,Parent,Parent GEM ID,Parent Perm ID,Location address,Municipality,Subnational unit (province/state),Country,Region,Other language location address,Coordinates,Coordinate accuracy,GEM wiki page,Capacity operating status,Plant age (years),Announced date,Construction date,Start date,Pre-retirement announcement date,Idled date,Retired date,Nominal crude steel capacity (ttpa),Nominal BOF steel capacity (ttpa),Nominal EAF steel capacity (ttpa),Nominal OHF steel capacity (ttpa),Other/unspecified steel capacity (ttpa),Nominal iron capacity (ttpa),Nominal BF capacity (ttpa),Nominal DRI capacity (ttpa),Other/unspecified iron capacity (ttpa),Ferronickel capacity (ttpa),Sinter plant capacity (ttpa),Coking plant capacity (ttpa),Pelletizing plant capacity (ttpa),Category steel product,Steel products,Steel sector end users,Workforce size,ISO 14001,ISO 50001,ResponsibleSteel Certification,Main production process,Main production equipment,Detailed production equipment,Power source,Iron ore source,Met coal source
        P100000120170,Anyang Huixin Special Steel Co Ltd,安阳汇鑫特钢有限公司,"Anyang Huixin Huacheng Special Steel Co., Ltd.; Anyang Huacheng Special Steel Co., Ltd.; Anyang Huacheng Steel Co., Ltd.; Anyang Jinxiu Steel Co., Ltd.",安阳汇鑫华诚特钢有限公司; 安阳华诚特钢有限公司; 安阳华诚钢铁有限责任公司; 安阳锦秀钢铁有限公司,"Anyang Huixin Special Steel Co., Ltd.",安阳汇鑫特钢有限公司,E100000126643,unknown,N/A,"Henan Jingchang Coal Chemical Co Ltd [90.0%]; Henan Xinlei Group HOLDINGS Co Ltd [6.2%]; Henan LIYUAN Coking Group Co Ltd [1.3%]; Henan Shuncheng Group,coal Co Ltd [1.3%]; Henan Yulong Coking Co Ltd [1.3%]",E100000127613 [90.0%]; E100000128836 [6.2%]; E100000128827 [1.3%]; E100000128828 [1.3%]; E100000128829 [1.3%],unknown,"Dongjiang Village, Shuiye Town, Anyang County, Anyang City, Henan Province",Anyang,Henan,China,Asia Pacific,河南省安阳市安阳县水冶镇东蒋村,"36.152418, 114.15839",exact,https://www.gem.wiki/Anyang_Huixin_Special_Steel_Co_Ltd,operating pre-retirement,20,unknown,unknown,2004,2023-06-07,N/A,2025,960,960,N/A,N/A,N/A,850,850,N/A,N/A,N/A,unknown,unknown,unknown,Semi-finished,steel billet,unknown,849,N/A,N/A,N/A,integrated (BF),"BF, BOF",2 BOF (2x40-tonne),,,
        P100000120160,Anyang Iron & Steel Co Ltd,安阳钢铁股份有限公司,,,"Anyang Iron & Steel Co., Ltd.",安阳钢铁股份有限公司,E100001000432,4295864803,unknown,Anyang Iron & Steel Co Ltd [100%],E100001000432 [100%],4295864803 [100%],"No. 502 Angang Avenue, Yindu District, Anyang City, Henan Province",Anyang,Henan,China,Asia Pacific,河南省安阳市殷都区安钢大道502号,"36.122129, 114.283145",exact,https://www.gem.wiki/Anyang_Iron_&_Steel_Co_Ltd,operating pre-retirement,N/A,N/A,N/A,N/A,unknown,unknown,unknown,750,N/A,750,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,finished rolled,"medium plate, steckel coiled plate, hot rolled coil plate, high speed wire and ductile iron pipe",unknown,unknown,N/A,N/A,N/A,electric,EAF,1 EAF (100-tonne),coal gas recovery power station&waste heat power generation unit,,
        P100000120029,Aperam Stainless Belgium Châtelet steel plant,,"Aperam Châtelet, Aperam - Carlam, Société Carolorégienne de Laminage (CARLAM) (predecessor), Carinox (predecessor)",,Aperam Stainless Belgium NV,,E100000130918,5000047720,N/A,Aperam SA [100.0%],E100000130966 [100.0%],5001428593 [100%],"14 rue des Ateliers, 6200 Châtelet, Belgium",Châtelet,Wallonie,Belgium,Europe,,"50.414998, 4.532443",exact,https://www.gem.wiki/Aperam_Stainless_Belgium_Châtelet_steel_plant,operating,48,unknown,unknown,1976,N/A,N/A,N/A,1000,N/A,1000,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,semi-finished; finished rolled,"stainless steel, slabs, cold rolled products",unknown,712,2022,N/A,2021-09,electric,EAF,1 EAF (160-tonne); 1 AOD converter (180-tonne),,,
    """
    return create_csv_in_path(tmp_path / "steel_plants_test.csv", steel_plant_csv_content)


@pytest.fixture
def historical_production_data_csv(tmp_path):
    """
    Write some steel plant data to a temporary CSV file.
    """
    historical_production_data_csv = """
        ,,2019,,,,,,,2020,,,,,,,2021,,,,,,,2022,,,,,,
        Plant ID,Plant name (English),Crude steel production 2019 (ttpa),BOF steel production 2019 (ttpa),EAF steel production 2019 (ttpa),OHF steel production 2019 (ttpa),Iron production 2019 (ttpa),BF production 2019 (ttpa),DRI production 2019 (ttpa),Crude steel production 2020 (ttpa),BOF steel production 2020 (ttpa),EAF steel production 2020 (ttpa),OHF steel production 2020 (ttpa),Iron production 2020 (ttpa),BF production 2020 (ttpa),DRI production 2020 (ttpa),Crude steel production 2021 (ttpa),BOF steel production 2021 (ttpa),EAF steel production 2021 (ttpa),OHF steel production 2021 (ttpa),Iron production 2021 (ttpa),BF production 2021 (ttpa),DRI production 2021 (ttpa),Crude steel production 2022 (ttpa),BOF steel production 2022 (ttpa),EAF steel production 2022 (ttpa),OHF steel production 2022 (ttpa),Iron production 2022 (ttpa),BF production 2022 (ttpa),DRI production 2022 (ttpa)
        P100000120170,Anyang Huixin Special Steel Co Ltd,unknown,unknown,N/A,N/A,unknown,unknown,unknown,unknown,unknown,N/A,N/A,unknown,unknown,N/A,unknown,unknown,N/A,N/A,unknown,unknown,N/A,unknown,unknown,N/A,N/A,unknown,unknown,N/A    P100000120007,TenarisSiderca Campana steel plant,878,N/A,878,N/A,unknown,N/A,unknown,694,N/A,694,N/A,>0,N/A,>0,873,N/A,873,N/A,>0,N/A,>0,922,N/A,922,N/A,882,N/A,882
        P100000120029,Aperam Stainless Belgium Châtelet steel plant,unknown,N/A,unknown,N/A,N/A,N/A,N/A,unknown,N/A,unknown,N/A,N/A,N/A,N/A,unknown,N/A,unknown,N/A,N/A,N/A,N/A,unknown,N/A,unknown,N/A,N/A,N/A,N/A
    """
    return create_csv_in_path(tmp_path / "historical_production_data_test.csv", historical_production_data_csv)


@pytest.fixture
def gravity_distances_csv(tmp_path):
    """
    Write some distance data to a temporary CSV file.
    """
    gravity_data_csv = """
        iso3_o,iso3_d,dist
        ABW,ABW,5
        ABW,AFG,13256
        ABW,AGO,9505
        ABW,AIA,978
        ABW,ALB,9090
        ABW,AND,7570
    """
    return create_csv_in_path(tmp_path / "gravity_distances_test.csv", gravity_data_csv)


@pytest.fixture
def iron_production_csv(tmp_path):
    """
    Write some iron production data to a temporary CSV file.
    """
    iron_production_csv_content = """
        Country,Technology,2019,2020,2021,2022
        Algeria,DRI,1540,2230,3080,3880
        Algeria,BF,300,300,300,300
        Argentina,DRI,1006,525,1408,1433
        Argentina,BF,1964,1930,2142,2060
        Australia,BF,3664,3723,3751,3652
        Austria,BF,5750,5322,6144,5803
        Bahrain,DRI,1450,1380,1510,1420    
    """
    return create_csv_in_path(tmp_path / "iron_production_test.csv", iron_production_csv_content)


@pytest.fixture
def steel_production_csv(tmp_path):
    """
    Write some steel production data to a temporary CSV file.
    """
    steel_production_csv_content = """
        Country,Technology,2019,2020,2021,2022
        Argentina,BOF,2.093,2.035,2.6852,2.2746
        Argentina,EAF,2.507,1.665,2.2148,2.8254
        Australia,BOF,4.026,4.07,4.2688,4.1895
        Australia,EAF,1.474,1.43,1.5312,1.5105
        Austria,BOF,6.6896,6.12,7.2127,6.825
        Austria,EAF,0.7104,0.68,0.6873,0.675
        Belgium,BOF,5.3274,4.1602,4.8093,0.3465
    """
    return create_csv_in_path(tmp_path / "steel_production_test.csv", steel_production_csv_content)
