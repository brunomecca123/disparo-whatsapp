"""Ponto de entrada das Vercel Functions.

O runtime Python da Vercel detecta o objeto ASGI exportado aqui e encaminha todas as
rotas para ele (ver o rewrite em vercel.json). O aplicativo é o mesmo que roda local:
o que muda é apenas o teto de tempo por execução (TIME_BUDGET_S).
"""

import sys
from pathlib import Path

# A função roda com a raiz do projeto como cwd, mas o import de `core` precisa dela no path.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app import app  # noqa: E402  (o ajuste de sys.path precisa vir antes)
