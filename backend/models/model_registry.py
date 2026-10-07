from pathlib import Path
import json
import joblib


class ModelRegistry:

    def __init__(self, model_path: str):
        self.model_path = Path(model_path)


    def load(self):

        model = joblib.load(
            self.model_path / "model.pkl"
        )

        with open(
            self.model_path / "metadata.json",
            "r",
        ) as file:
            metadata = json.load(file)

        return {
            "model": model,
            "metadata": metadata,
        }