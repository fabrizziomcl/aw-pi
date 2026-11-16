import sys
from pathlib import Path

# scraping/ son scripts sueltos (sin paquete): se importan por nombre de módulo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scraping"))
