import yaml
from typing import Any, Dict


class YamlReader:
    """
    Static utility class for reading YAML files.
    """

    @staticmethod
    def read_yaml(file_path: str, encoding: str = 'utf-8') -> Dict[str, Any]:
        """
        Safely read a YAML file and return its contents as a dictionary.

        Args:
            file_path (str): The path to the YAML file.
            encoding (str): The encoding to use when reading the file. Defaults to 'utf-8'.

        Returns:
            Dict[str, Any]: The parsed YAML content as a dictionary.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If there's an error parsing the YAML.
        """
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                return yaml.safe_load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"YAML file not found: {file_path}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Error parsing YAML file {file_path}: {e}")
