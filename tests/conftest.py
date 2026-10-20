import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# os testes nunca usam uma chave real da Anthropic nem o Clair real
for _k in ("COLLABSIM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)
