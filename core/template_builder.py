"""Validação e montagem de templates novos para a Meta.

Portado do criador_template_meta (meta_client.py). O cabeçalho pode ser texto ou mídia
(imagem, vídeo, documento); a mídia chega aqui já como handle do exemplo enviado à Meta.
A tela já escreve as variáveis no formato da Meta ({{1}}, {{2}}…); a conversão continua
aceitando {{nome}} e deixa tudo em sequência na ordem do texto, caso chegue algo fora disso.
"""

import re
from typing import Dict, List, Optional, Tuple

from core.meta_api import PLACEHOLDER_RE

# Limites da Meta para templates (documentação oficial da Cloud API).
MAX_NAME_LENGTH = 512
MAX_HEADER_LENGTH = 60
MAX_BODY_LENGTH = 1024
MAX_FOOTER_LENGTH = 60
MAX_HEADER_VARIABLES = 1
MAX_BUTTONS = 10
MAX_URL_BUTTONS = 2
MAX_PHONE_BUTTONS = 1
MAX_BUTTON_TEXT = 25
MAX_URL_LENGTH = 2000

# AUTHENTICATION tem estrutura própria (código OTP pré-definido pela Meta), não serve
# para o formulário de texto livre.
VALID_CATEGORIES = ("MARKETING", "UTILITY")
VALID_LANGUAGES = ("pt_BR", "pt_PT", "en_US", "es_ES", "es_MX")
BUTTON_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER")
# Cabeçalho de mídia: vai com um exemplo já enviado à Meta (handle da Resumable Upload API).
MEDIA_HEADER_FORMATS = ("IMAGE", "VIDEO", "DOCUMENT")
HEADER_TYPES = ("NONE", "TEXT") + MEDIA_HEADER_FORMATS
MEDIA_HEADER_LABELS = {"IMAGE": "imagem", "VIDEO": "vídeo", "DOCUMENT": "documento (PDF)"}

NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
PHONE_PATTERN = re.compile(r"^\+?\d{8,20}$")
# Qualquer "{{" ou "}}" que sobrar depois de tirar as variáveis válidas é erro de digitação.
BROKEN_BRACES = re.compile(r"\{\{|\}\}")

# Acentos e cedilha: se aparecem, o texto quase certamente não é en_US.
PORTUGUESE_CHARS = "áéíóúãõâêôàçÁÉÍÓÚÃÕÂÊÔÀÇ"


def unique_variables(text: str) -> List[str]:
    """Nomes distintos das variáveis, preservando a ordem de primeira aparição."""
    vistos: Dict[str, None] = {}
    for nome in PLACEHOLDER_RE.findall(text or ""):
        vistos.setdefault(nome, None)
    return list(vistos)


def convert_named_to_indexed(text: str) -> Tuple[str, List[str]]:
    """Troca {{nome}} por {{1}}, {{2}}... na ordem de primeira aparição.

    Ocorrências repetidas da mesma variável recebem o mesmo índice. Devolve o texto
    convertido e a lista de nomes na ordem dos índices.
    """
    mapa: Dict[str, int] = {}

    def troca(match: re.Match) -> str:
        nome = match.group(1)
        if nome not in mapa:
            mapa[nome] = len(mapa) + 1
        return "{{%d}}" % mapa[nome]

    return PLACEHOLDER_RE.sub(troca, text or ""), list(mapa)


def variable_at_edge(text: str) -> Tuple[bool, bool]:
    """Diz se o texto começa e/ou termina com uma variável.

    A Meta recusa (código 100, subcódigo 2388299) corpo que começa ou termina com
    variável: sem texto fixo em volta, o revisor não consegue julgar o conteúdo.
    """
    limpo = (text or "").strip()
    if not limpo:
        return False, False
    comeca = PLACEHOLDER_RE.match(limpo)
    termina = None
    for termina in PLACEHOLDER_RE.finditer(limpo):
        pass
    return bool(comeca), bool(termina and termina.end() == len(limpo))


def _tem_chave_quebrada(text: str) -> bool:
    return bool(BROKEN_BRACES.search(PLACEHOLDER_RE.sub("", text or "")))


def _exemplos(nomes: List[str], valores: Dict[str, str]) -> List[str]:
    return [str(valores.get(nome, "") or "").strip() for nome in nomes]


def _validar_texto_com_variaveis(rotulo: str, texto: str, exemplos: Dict[str, str], erros: List[str]):
    if _tem_chave_quebrada(texto):
        erros.append(f"{rotulo}: variável mal escrita — use {{{{1}}}}, {{{{2}}}}…")
    for nome, valor in zip(unique_variables(texto), _exemplos(unique_variables(texto), exemplos)):
        if not valor:
            erros.append(f"{rotulo}: falta o exemplo de {{{{{nome}}}}}.")


def _botoes_ordenados(botoes: List[Dict]) -> List[Dict]:
    """Respostas rápidas primeiro, depois os de ação.

    A Meta exige que cada tipo fique agrupado; mantendo a ordem relativa dentro de cada
    grupo, o usuário não precisa se preocupar com isso.
    """
    return [b for b in botoes if b.get("type") == "QUICK_REPLY"] + [
        b for b in botoes if b.get("type") != "QUICK_REPLY"
    ]


def validate(dados: Dict) -> Tuple[List[str], List[str]]:
    """Devolve (erros, avisos). Sem erros, o template está pronto para ir à Meta."""
    erros: List[str] = []
    avisos: List[str] = []

    nome = dados.get("name") or ""
    if not nome:
        erros.append("Nome vazio.")
    elif not NAME_PATTERN.match(nome):
        erros.append("Nome deve conter apenas letras minúsculas (sem acento), números e _.")
    elif len(nome) > MAX_NAME_LENGTH:
        erros.append(f"Nome tem {len(nome)} caracteres (máximo {MAX_NAME_LENGTH}).")

    categoria = dados.get("category") or ""
    if categoria == "AUTHENTICATION":
        erros.append("Templates de autenticação (código OTP) têm formato próprio: crie pelo WhatsApp Manager.")
    elif categoria not in VALID_CATEGORIES:
        erros.append("Escolha a categoria: MARKETING ou UTILITY.")

    idioma = dados.get("language") or ""
    if idioma not in VALID_LANGUAGES:
        erros.append(f"Idioma inválido: {idioma or '—'}.")

    header = dados.get("header") or {}
    header_tipo = (header.get("type") or "NONE").upper()
    header_texto = (header.get("text") or "").strip() if header_tipo == "TEXT" else ""
    if header_tipo not in HEADER_TYPES:
        erros.append(f"Tipo de cabeçalho inválido: {header_tipo}.")
    elif header_tipo in MEDIA_HEADER_FORMATS and not (header.get("handle") or "").strip():
        erros.append(f"Cabeçalho de {MEDIA_HEADER_LABELS[header_tipo]}: escolha o arquivo de exemplo "
                     "(a Meta só analisa o template com ele).")
    if header_tipo == "TEXT":
        if not header_texto:
            erros.append("Cabeçalho de texto vazio — preencha ou escolha 'sem cabeçalho'.")
        elif len(header_texto) > MAX_HEADER_LENGTH:
            erros.append(f"Cabeçalho tem {len(header_texto)} caracteres (máximo {MAX_HEADER_LENGTH}).")
        if "\n" in header_texto:
            erros.append("Cabeçalho não pode ter quebra de linha.")
        if len(unique_variables(header_texto)) > MAX_HEADER_VARIABLES:
            erros.append(f"Cabeçalho aceita no máximo {MAX_HEADER_VARIABLES} variável.")
        _validar_texto_com_variaveis("Cabeçalho", header_texto, header.get("examples") or {}, erros)

    body = dados.get("body") or {}
    body_texto = (body.get("text") or "").strip()
    if not body_texto:
        erros.append("Corpo da mensagem vazio.")
    else:
        if len(body_texto) > MAX_BODY_LENGTH:
            erros.append(f"Corpo tem {len(body_texto)} caracteres (máximo {MAX_BODY_LENGTH}).")
        comeca, termina = variable_at_edge(body_texto)
        if comeca:
            erros.append("O corpo não pode começar com variável; escreva algum texto antes.")
        if termina:
            erros.append("O corpo não pode terminar com variável; escreva algum texto depois (nem que seja um ponto).")
        _validar_texto_com_variaveis("Corpo", body_texto, body.get("examples") or {}, erros)

    rodape = (dados.get("footer") or "").strip()
    if len(rodape) > MAX_FOOTER_LENGTH:
        erros.append(f"Rodapé tem {len(rodape)} caracteres (máximo {MAX_FOOTER_LENGTH}).")
    if PLACEHOLDER_RE.search(rodape):
        erros.append("Rodapé não aceita variáveis.")

    botoes = dados.get("buttons") or []
    if len(botoes) > MAX_BUTTONS:
        erros.append(f"No máximo {MAX_BUTTONS} botões.")
    if sum(b.get("type") == "URL" for b in botoes) > MAX_URL_BUTTONS:
        erros.append(f"No máximo {MAX_URL_BUTTONS} botões de link.")
    if sum(b.get("type") == "PHONE_NUMBER" for b in botoes) > MAX_PHONE_BUTTONS:
        erros.append(f"No máximo {MAX_PHONE_BUTTONS} botão de telefone.")

    textos_vistos = set()
    for posicao, botao in enumerate(botoes, start=1):
        rotulo = f"Botão {posicao}"
        tipo = botao.get("type")
        texto = (botao.get("text") or "").strip()
        if tipo not in BUTTON_TYPES:
            erros.append(f"{rotulo}: tipo inválido.")
            continue
        if not texto:
            erros.append(f"{rotulo}: falta o texto.")
        elif len(texto) > MAX_BUTTON_TEXT:
            erros.append(f"{rotulo}: texto tem {len(texto)} caracteres (máximo {MAX_BUTTON_TEXT}).")
        elif texto.lower() in textos_vistos:
            erros.append(f"{rotulo}: já existe outro botão com o texto \"{texto}\".")
        textos_vistos.add(texto.lower())

        if tipo == "URL":
            url = (botao.get("url") or "").strip()
            variaveis = PLACEHOLDER_RE.findall(url)
            if not re.match(r"^https?://", url):
                erros.append(f"{rotulo}: o link precisa começar com https://.")
            elif len(url) > MAX_URL_LENGTH:
                erros.append(f"{rotulo}: link longo demais.")
            if _tem_chave_quebrada(url):
                erros.append(f"{rotulo}: variável mal escrita no link.")
            if len(variaveis) > 1:
                erros.append(f"{rotulo}: o link aceita só uma variável.")
            elif variaveis and list(PLACEHOLDER_RE.finditer(url))[-1].end() != len(url):
                erros.append(f"{rotulo}: a variável do link precisa ficar no final (ex.: https://site.com/pedido/{{{{1}}}}).")
            elif variaveis and not (botao.get("example") or "").strip():
                erros.append(f"{rotulo}: falta o exemplo do final do link.")
        elif tipo == "PHONE_NUMBER":
            telefone = re.sub(r"[\s()-]", "", botao.get("phone") or "")
            if not PHONE_PATTERN.match(telefone):
                erros.append(f"{rotulo}: telefone inválido — use DDI + DDD + número (ex.: +5544999999999).")

    if idioma == "en_US" and any(c in f"{header_texto} {body_texto}" for c in PORTUGUESE_CHARS):
        avisos.append("O texto parece português, mas o idioma está en_US — a Meta costuma rejeitar.")
    if categoria == "UTILITY" and re.search(r"promo|desconto|oferta|black friday|cupom", body_texto, re.I):
        avisos.append("O texto tem cara de promoção: a Meta pode reclassificar como MARKETING (e cobrar como tal).")
    if body_texto and len(unique_variables(body_texto)) * 12 > len(PLACEHOLDER_RE.sub("", body_texto)):
        avisos.append("Muitas variáveis para pouco texto fixo — a Meta rejeita templates assim. Escreva mais contexto.")

    return erros, avisos


def build_payload(dados: Dict) -> Dict:
    """Monta o corpo do POST /message_templates, com variáveis já indexadas."""
    componentes: List[Dict] = []

    header = dados.get("header") or {}
    header_tipo = (header.get("type") or "").upper()
    if header_tipo in MEDIA_HEADER_FORMATS:
        componente = {"type": "HEADER", "format": header_tipo}
        handle = (header.get("handle") or "").strip()
        if handle:
            componente["example"] = {"header_handle": [handle]}
        componentes.append(componente)
    elif header_tipo == "TEXT" and (header.get("text") or "").strip():
        original = header["text"].strip()
        texto, nomes = convert_named_to_indexed(original)
        componente = {"type": "HEADER", "format": "TEXT", "text": texto}
        if nomes:
            # Header espera um array simples de valores.
            componente["example"] = {"header_text": _exemplos(nomes, header.get("examples") or {})}
        componentes.append(componente)

    body = dados.get("body") or {}
    texto, nomes = convert_named_to_indexed((body.get("text") or "").strip())
    componente = {"type": "BODY", "text": texto}
    if nomes:
        # Body espera um array de arrays (um por conjunto de exemplos).
        componente["example"] = {"body_text": [_exemplos(nomes, body.get("examples") or {})]}
    componentes.append(componente)

    rodape = (dados.get("footer") or "").strip()
    if rodape:
        componentes.append({"type": "FOOTER", "text": rodape})

    botoes = []
    for botao in _botoes_ordenados(dados.get("buttons") or []):
        item = {"type": botao["type"], "text": (botao.get("text") or "").strip()}
        if botao["type"] == "URL":
            url, nomes = convert_named_to_indexed((botao.get("url") or "").strip())
            item["url"] = url
            if nomes:
                # O exemplo é o link completo, com o final preenchido.
                base = PLACEHOLDER_RE.sub("", (botao.get("url") or "").strip())
                item["example"] = [base + (botao.get("example") or "").strip()]
        elif botao["type"] == "PHONE_NUMBER":
            item["phone_number"] = re.sub(r"[\s()-]", "", botao.get("phone") or "")
        botoes.append(item)
    if botoes:
        componentes.append({"type": "BUTTONS", "buttons": botoes})

    return {
        "name": dados.get("name"),
        "category": dados.get("category"),
        "language": dados.get("language"),
        "components": componentes,
    }


def error_hint(mensagem: str) -> Optional[str]:
    """Dica em português para os erros de criação que mais aparecem na prática."""
    texto = (mensagem or "").lower()
    if "already exists" in texto or "duplicate" in texto or "já existe" in texto:
        return "Já existe um template com esse nome e idioma na conta — use outro nome."
    if "being deleted" in texto or "sendo excluído" in texto:
        return "Um template com esse nome foi excluído há pouco; a Meta bloqueia o nome por 4 semanas."
    if "language" in texto or "idioma" in texto:
        return "Verifique o código de idioma (pt_BR para português do Brasil)."
    if "header_handle" in texto or "handle" in texto:
        return "O exemplo do cabeçalho não foi aceito: suba o arquivo de novo (o handle expira) e reenvie."
    if "example" in texto or "exemplo" in texto:
        return "Faltou exemplo para alguma variável, ou a quantidade não bate."
    if "rate" in texto or "limit" in texto:
        return "Limite da Meta atingido (100 criações por hora). Espere e tente de novo."
    return None
