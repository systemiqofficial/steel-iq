from typing import Callable


class EnvironmentScorer:
    @staticmethod
    def normalize_and_score(
        grid: list[dict[str, float]],
        distance_columns: list[str],
        scaler: Callable[[list[list[float]]], list[list[float]]],
    ) -> list[dict[str, float]]:
        """
        Normalize distances and compute proximity scores.

        Args:
            grid (list[dict[str, float]]): The grid with distance columns as a list of dictionaries.
            distance_columns (list[str]): list of column names to normalize.
            scaler (Callable): A scaling function provided by adapters.

        Returns:
            list[dict[str, float]]: Grid with normalized and scored data as a list of dictionaries.
        """
        # Prepare data for scaling
        data = [[row[col] for col in distance_columns] for row in grid]
        normalized = scaler(data)

        for i, row in enumerate(grid):
            for j, col in enumerate(distance_columns):
                row[f"{col}_norm"] = normalized[i][j]

            row["average_score"] = sum(row[f"{col}_norm"] for col in distance_columns) / len(distance_columns)
            row["proximity_score"] = 1 - row["average_score"]

        return grid
