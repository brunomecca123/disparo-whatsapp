"""
Configuração da Campanha de WhatsApp

INSTRUÇÕES:
- Edite este arquivo para personalizar sua campanha
- Não precisa mexer no código principal (whatsapp_campaign.py)
"""

# ============================================
# CONFIGURAÇÕES DA API DO WHATSAPP (META)
# ============================================

# Versão da API do Facebook Graph
API_VERSION = "v19.0"

# ============================================
# CONFIGURAÇÕES DO TEMPLATE
# ============================================

# Nome do template no WhatsApp (deve estar aprovado no Meta Business)
TEMPLATE_NAME = "nova_proposta_renegociacao_13_ago_2026"

# Código do idioma do template
LANGUAGE_CODE = "pt_BR"

# Tipo do header do template: "none", "text", "document" ou "image"
HEADER_TYPE = "text"

# ============ CONFIGURAÇÕES PARA HEADER TYPE = IMAGE ============

# --- Opção 1: caminho local do arquivo de imagem ---
# O script fará o upload automático para o Meta na primeira execução
# e armazenará o ID retornado em HEADER_IMAGE_ID abaixo.
HEADER_IMAGE_PATH = ""  # ex: "/home/user/imagens/banner.png"

# --- Opção 2: Media ID já obtido de um upload anterior ---
# Se preenchido, o upload é ignorado e este ID é usado diretamente.
# Deixe como string vazia ("") para forçar o upload do arquivo local.
HEADER_IMAGE_ID = ""

# --- Opção 3: URL pública da imagem (HTTPS) ---
# Só usado se HEADER_IMAGE_ID e HEADER_IMAGE_PATH estiverem vazios.
HEADER_IMAGE_URL = ""

# ============ CONFIGURAÇÕES PARA HEADER TYPE = VIDEO ============

# --- Opção 1: caminho local do arquivo de vídeo ---
# O script fará o upload automático para o Meta na primeira execução
# e armazenará o ID retornado em HEADER_VIDEO_ID abaixo.
HEADER_VIDEO_PATH = ""  # ex: "/home/user/videos/apresentacao.mp4" ou "Pablo.mp4"

# --- Opção 2: Media ID já obtido de um upload anterior ---
# Se preenchido, o upload é ignorado e este ID é usado diretamente.
# Deixe como string vazia ("") para forçar o upload do arquivo local.
HEADER_VIDEO_ID = ""  # Substitua com seu Media ID após fazer upload (ex: 123456789)

# --- Opção 3: URL pública do vídeo (HTTPS) ---
# Só usado se HEADER_VIDEO_ID e HEADER_VIDEO_PATH estiverem vazios.
HEADER_VIDEO_URL = "" # "https://www.dropbox.com/scl/fi/hhjsb7cu7jmgxseeudydj/Pablo.mp4?dl=1"  # ex: "https://example.com/video.mp4"

# ============ CONFIGURAÇÕES PARA HEADER TYPE = DOCUMENT ============

# --- Opção 1: caminho local do arquivo de documento ---
# O script fará o upload automático para o Meta na primeira execução
# e armazenará o ID retornado em HEADER_DOCUMENT_ID abaixo.
HEADER_DOCUMENT_PATH = ""  # ex: "/home/user/documentos/contrato.pdf"

# --- Opção 2: Media ID já obtido de um upload anterior ---
# Se preenchido, o upload é ignorado e este ID é usado diretamente.
# Deixe como string vazia ("") para forçar o upload do arquivo local.
HEADER_DOCUMENT_ID = ""

# --- Opção 3: URL pública do documento (HTTPS) ---
# Só usado se HEADER_DOCUMENT_ID e HEADER_DOCUMENT_PATH estiverem vazios.
HEADER_DOCUMENT_URL = ""

# ============================================
# CONFIGURAÇÕES DO CSV
# ============================================

# Nome do arquivo CSV com os dados
CSV_FILE = "clientes.csv"

# Delimitador usado no CSV
CSV_DELIMITER = ","

# Mapeamento das colunas do CSV para as variáveis do template
# As chaves são os nomes das colunas no CSV
# Os valores indicam em qual parte do template a variável será usada e sua ordem
#
# Formato: "nome_coluna_csv": {"section": "header|body", "position": 0}
#
# EXEMPLO para o template campanha_fechamais:
# Header: {{1}} = nome do cliente
# Body: {{1}} = endereço, {{2}} = imobiliária, {{3}} = link, {{4}} = whatsapp
CSV_COLUMN_MAPPING = {
    "telefone": {"section": "phone", "position": 0},  # Coluna obrigatória com o telefone
    # "nome": {"section": "header", "position": 0},     # Primeiro parâmetro do header
    # "contrato": {"section": "body", "position": 0},   # Primeiro parâmetro do body
    #"imobiliaria": {"section": "body", "position": 1}, # Segundo parâmetro do body
    # "link": {"section": "body", "position": 1},       # Terceiro parâmetro do body
    # "whatsapp_contato": {"section": "body", "position": 3}, # Quarto parâmetro do body
}

# ============================================
# CONFIGURAÇÕES DE EXECUÇÃO
# ============================================

# Intervalo entre envios (em segundos) para evitar rate limiting
SEND_INTERVAL = 1.0

# Mostrar progresso a cada X mensagens
PROGRESS_INTERVAL = 10

# Continuar em caso de erro individual (True) ou parar (False)
CONTINUE_ON_ERROR = True

# Modo de teste (True = não envia mensagens, apenas simula)
DRY_RUN = False

# ============================================
# CONFIGURAÇÕES DE LOG
# ============================================

# Arquivo de log para registrar erros e sucessos
LOG_FILE = "whatsapp_campaign.log"

# Nível de log (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL = "INFO"
