import math
from pathlib import Path
from wind_and_pv.chunking import GeoChunk, GeoChunker


def test_geo_chunk_naming():
    chunk = GeoChunk(x=slice(-10, -5), y=slice(50, 55), time="2024", chunks_dir=Path("/tmp/chunks"))
    name = chunk.get_prefixed_name("weather")
    assert name == "weather_-10_-5_50_55.nc"
    assert chunk.get_path("weather") == Path("/tmp/chunks/weather_-10_-5_50_55.nc")


def test_geo_chunker_chunk_count():
    chunker = GeoChunker(
        x_chunk_size=10,
        y_chunk_size=10,
        time="2024",
        x_min=0,
        x_max=20,
        y_min=30,
        y_max=50,
        chunks_dir=Path("/tmp/chunks"),
    )
    chunks = chunker.chunks
    assert len(chunks) == 4

    actual_xs = [(chunk.x.start, chunk.x.stop) for chunk in chunks]
    actual_ys = [(chunk.y.start, chunk.y.stop) for chunk in chunks]

    expected_xs = [(0.0, 10.0), (10.0, 20.0)]
    expected_ys = [(50.0, 40.0), (40.0, 30.0)]

    for expected in expected_xs:
        assert any(
            math.isclose(actual[0], expected[0], abs_tol=1e-6) and math.isclose(actual[1], expected[1], abs_tol=1e-6)
            for actual in actual_xs
        ), f"Missing expected x-slice: {expected}"

    for expected in expected_ys:
        assert any(
            math.isclose(actual[0], expected[0], abs_tol=1e-6) and math.isclose(actual[1], expected[1], abs_tol=1e-6)
            for actual in actual_ys
        ), f"Missing expected y-slice: {expected}"


def test_chunk_edges_exactly_on_bounds():
    chunker = GeoChunker(x_chunk_size=10, y_chunk_size=10, time="2024", x_min=0, x_max=10, y_min=40, y_max=50)
    chunks = chunker.chunks
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.x == slice(0, 10)
    assert chunk.y == slice(50, 40)


def test_no_overlap_due_to_delta():
    chunker = GeoChunker(x_chunk_size=5, y_chunk_size=5, time="2024", x_min=0, x_max=10, y_min=0, y_max=10)
    slices_x = [chunk.x for chunk in chunker.chunks]
    slices_y = [chunk.y for chunk in chunker.chunks]
    for s in slices_x:
        assert s.stop - s.start <= 5.0
    for s in slices_y:
        assert abs(s.start - s.stop) <= 5.0
