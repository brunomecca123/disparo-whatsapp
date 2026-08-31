# 🎥 Configuração de Vídeo no WhatsApp Campaign

Guia rápido para configurar envio de mensagens com vídeo no header.

## 3 Opções de Configuração

### ✅ Opção 1: Usar Media ID (RECOMENDADO)

Se você já fez upload de um vídeo e tem o Media ID:

```python
# config.py
HEADER_VIDEO_ID = "123456789"  # Cole seu Media ID aqui
HEADER_VIDEO_PATH = ""
HEADER_VIDEO_URL = ""
```

**Vantagem:** Rápido, sem novo upload.

---

### 📤 Opção 2: Upload Automático (PATH Local)

Para fazer upload de um arquivo local:

```python
# config.py
HEADER_VIDEO_PATH = "Pablo.mp4"  # ou "/caminho/completo/video.mp4"
HEADER_VIDEO_ID = ""
HEADER_VIDEO_URL = ""
```

**Na primeira execução:**
1. Script faz upload do arquivo
2. Exibe o Media ID retornado
3. Você salva esse ID em `HEADER_VIDEO_ID` para próximos envios

**Dicas:**
- Arquivo máximo: 100MB (limite Meta)
- Formatos: MP4, MOV
- Timeout aumentado para 10 minutos (uploads grandes)
- Retry automático em caso de erro de conexão

---

### 🔗 Opção 3: URL Pública

Para usar uma URL HTTPS pública:

```python
# config.py
HEADER_VIDEO_URL = "https://example.com/video.mp4"
HEADER_VIDEO_PATH = ""
HEADER_VIDEO_ID = ""
```

**Requisitos:**
- URL precisa ser HTTPS (não HTTP)
- Vídeo precisa estar acessível (sem autenticação)

---

## Fluxo Recomendado

### Primeira Vez (com arquivo local)

```python
# config.py
HEADER_TYPE = "video"
HEADER_VIDEO_PATH = "Pablo.mp4"
HEADER_VIDEO_ID = ""
HEADER_VIDEO_URL = ""
DRY_RUN = False
```

```bash
# Execute
python whatsapp_campaign.py
```

**Resultado esperado:**
```
⬆️  Fazendo upload do vídeo: Pablo.mp4
✓ Upload concluído. Media ID: 123456789
  Dica: salve este ID em HEADER_VIDEO_ID no config.py para evitar uploads futuros.
```

### Próximos Envios (reutilizar Media ID)

```python
# config.py
HEADER_VIDEO_ID = "123456789"  # Cole o ID obtido acima
HEADER_VIDEO_PATH = ""
HEADER_VIDEO_URL = ""
```

---

## Troubleshooting

### ❌ "Connection aborted. TimeoutError"

**Causa:** Arquivo muito grande ou conexão lenta.

**Solução:**
- Script agora tem timeout de 10 minutos (600 segundos)
- Retry automático em falhas
- Se continuar: use URL ou Media ID pré-existente

### ❌ "Invalid parameter"

**Causa:** Token expirado ou Phone ID inválido.

**Solução:**
- Verifique `WHATSAPP_TOKEN` e `WHATSAPP_PHONE_ID` em config.py
- Gere novo token em Meta Business Suite

### ❌ "Template not found"

**Causa:** Template não criado ou não aprovado.

**Solução:**
1. Acesse Meta Business Suite
2. WhatsApp > Message Templates
3. Crie/aprove template com nome: `envio_video_apresentacao_27_abr_2026`
4. Ou atualize `TEMPLATE_NAME` em config.py

---

## Dicas Importantes

✅ **Sempre teste com `DRY_RUN = True` primeiro**

✅ **Salve Media IDs** após upload bem-sucedido (economia de banda)

✅ **Use URL pública** para arquivos grandes (>50MB)

✅ **Comprima vídeos** se possível (reduz tamanho/tempo upload)

✅ **Verifique template** no Meta Business antes de enviar

---

## Campos de Config

| Campo | Uso | Prioridade |
|-------|-----|-----------|
| `HEADER_VIDEO_ID` | Media ID pré-existente | 1️⃣ Máxima |
| `HEADER_VIDEO_PATH` | Arquivo local (faz upload) | 2️⃣ Média |
| `HEADER_VIDEO_URL` | URL HTTPS pública | 3️⃣ Mínima |

Se deixar todos vazios → **erro de validação**

---

## Exemplo Completo

```python
# config.py
HEADER_TYPE = "video"
TEMPLATE_NAME = "envio_video_apresentacao_27_abr_2026"
WHATSAPP_TOKEN = "seu_token_aqui"
WHATSAPP_PHONE_ID = "seu_phone_id_aqui"

# Usar Media ID pré-existente
HEADER_VIDEO_ID = "123456789"
HEADER_VIDEO_PATH = ""
HEADER_VIDEO_URL = ""

# Arquivo CSV
CSV_FILE = "clientes.csv"
DRY_RUN = False
SEND_INTERVAL = 1.0
```

```bash
python whatsapp_campaign.py
```

---

**Desenvolvido para Sistema Locarmais** | 2026
