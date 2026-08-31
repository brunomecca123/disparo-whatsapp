# 🚀 DisparoMais — Campanhas de WhatsApp

Duas formas de disparar a mesma campanha:

| | Aplicativo web (`app.py`) | Script de terminal (`whatsapp_campaign.py`) |
|---|---|---|
| Escolher template | lista os aprovados direto da Meta | digitar o nome na mão em `config.py` |
| Variáveis | detectadas automaticamente do template | mapear na mão em `CSV_COLUMN_MAPPING` |
| Contatos | upload de CSV/Excel ou colar telefones | editar `clientes.csv` |
| Validação | dedup, DDD, 9º dígito, blocklist | nenhuma |
| Acompanhamento | barra de progresso na tela, CSV de resultado | log no terminal |

O script antigo continua funcionando sem alteração. O recomendado para o dia a dia é o app.

---


## Velocidade de envio e limites da Meta

O disparo é paralelo, com a taxa controlada em **mensagens por segundo** (campo *Velocidade* no
passo 4). Padrão: **8 msg/s**; teto da interface: 30 msg/s.

| Limite | Valor | Origem |
|---|---|---|
| Throughput do número (nível STANDARD) | 80 msg/s | Cloud API overview |
| Mesma pessoa | 1 msg a cada 6s | pair rate limit |
| Chamadas de gerenciamento (templates etc.) | 5.000/h por WABA ativa | Graph API rate limiting |

Quando a Meta responde `130429` (rate limit), o motor corta a taxa pela metade, espera e
**re-tenta o mesmo contato** — nenhuma mensagem é perdida —, voltando devagar à velocidade
configurada. O painel de progresso mostra a taxa real, quantos envios simultâneos estão em voo,
quantos freios ocorreram e o percentual da cota horária informado pelo cabeçalho
`X-Business-Use-Case-Usage`.

Velocidade não é o que gera banimento: o que derruba a qualidade do número são bloqueios e
denúncias de quem recebe. Vale conferir o *messaging limit tier* da conta antes de listas
grandes — ele limita quantas conversas iniciadas pela empresa cabem em 24h.

## 🖥️ Aplicativo web

### Instalação (uma vez)

```bash
sudo apt install python3.12-venv      # se ainda não tiver
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### Configuração

As credenciais ficam no `.env` (que **não** vai para o git):

```
WHATSAPP_TOKEN=...        # token da Cloud API
WHATSAPP_PHONE_ID=...     # id do número remetente
WHATSAPP_WABA_ID=...      # id da conta WhatsApp Business — necessário para listar templates
```

O `WHATSAPP_WABA_ID` está em business.facebook.com → WhatsApp Manager → Configurações da conta → ID
da conta. Também dá para colar ele na própria tela do app na primeira vez: o campo aparece quando a
lista de templates não carrega, e o valor é gravado no `.env` automaticamente.

### Uso

```bash
./venv/bin/python app.py
```

Abre em http://127.0.0.1:8777 (mude com `APP_PORT=9000 ./venv/bin/python app.py`).

O fluxo na tela é:

1. **Template** — escolha na lista; o app mostra status de aprovação, categoria, tipo de header e
   quantas variáveis existem. Se o header for imagem/vídeo/documento, suba o arquivo ali mesmo
   (o upload para a Meta é feito na hora) ou informe uma URL pública.
2. **Contatos** — suba o CSV/Excel recebido ou cole os telefones. O app detecta as colunas, chuta
   qual é a do telefone e monta um seletor para cada variável do template (coluna do arquivo ou
   valor fixo).
3. **Validação** — mostra quantos vão receber, quantos têm aviso, quantos são inválidos, duplicados
   ou estão na blocklist, com a lista detalhada de cada caso. A pré-visualização à direita mostra a
   mensagem já preenchida com os dados reais dos primeiros contatos.
4. **Disparo** — dá para testar em um número antes, ligar o modo simulação (monta o payload sem
   enviar) e ajustar o intervalo entre envios. O envio roda em background com progresso ao vivo,
   pode ser cancelado no meio, e no fim permite baixar o CSV de resultado ou reenviar só as falhas.

### Validação de telefones

- Normaliza para o formato E.164 (`5544991628511`), aceitando `(44) 99162-8511`, `+55...` etc.
- Rejeita DDD inexistente, quantidade errada de dígitos e números repetitivos (`43000000000`).
- Avisa sobre celular sem o 9º dígito e oferece adicionar automaticamente.
- Avisa quando o número parece telefone fixo.
- Remove duplicados dentro da mesma lista.

### Blocklist (opt-out)

Crie um `blocklist.txt` na raiz com um telefone por linha (linhas iniciadas por `#` são ignoradas).
Esses números são removidos de toda campanha, e o app informa quantos foram bloqueados.

### Arquivos

```
app.py                 rotas da aplicação web
core/settings.py       credenciais lidas do .env
core/meta_api.py       cliente da Graph API (templates, upload de mídia, envio)
core/contacts.py       leitura de CSV/Excel e validação de telefones
core/campaign.py       execução do disparo, ritmo e histórico
core/db.py             estado no Supabase (campanhas, destinatários, uploads)
core/storage.py        mídia grande: navegador → Supabase Storage → Meta
static/index.html      interface
api/index.py           ponto de entrada das funções da Vercel
```

### Deploy na Vercel

O mesmo aplicativo roda como função serverless, com três diferenças que o código já trata:

- **Envio em lotes.** A função é congelada assim que responde, então o disparo roda dentro
  da requisição até `TIME_BUDGET_S` (use `270`) e a invocação chama a si mesma para
  continuar. Um `UPDATE` condicional no banco é a trava: duas invocações nunca disparam
  para os mesmos pendentes, e uma execução cortada pela plataforma é retomada quando o
  batimento (`heartbeat_at`) envelhece.
- **Mídia acima de 4,5 MB** (limite de corpo da requisição na Vercel) sobe do navegador
  direto para o bucket `media` do Supabase Storage por URL assinada; o servidor busca de
  lá e entrega à Meta. Vídeo de template chega a 16 MB e só passa por esse caminho.
- **Nada é gravado em disco:** arquivos temporários vão para `/tmp` e `WHATSAPP_WABA_ID`
  precisa ser cadastrado em Settings → Environment Variables, não pela interface.

---

## 📟 Script de terminal (modo antigo)

Script Python para envio em massa de mensagens WhatsApp baseado na migration do Laravel.

## 📋 Características

- ✅ Lê dados de arquivo CSV
- ✅ Envia mensagens via API do WhatsApp (Meta)
- ✅ Suporta templates com variáveis personalizadas
- ✅ Mostra progresso em tempo real no terminal
- ✅ Normaliza números de telefone automaticamente
- ✅ Log detalhado de erros e sucessos
- ✅ Modo de teste (dry run)
- ✅ Configuração fácil via arquivo separado
- ✅ Tratamento de erros robusto
- ✅ Resumo detalhado ao final

## 📦 Instalação

### 1. Instalar Python 3.8+

Certifique-se de ter Python 3.8 ou superior instalado:

```bash
python3 --version
```

### 2. Instalar Dependências

```bash
cd scripts/whatsapp_campaign
pip install -r requirements.txt
```

Ou usando virtualenv (recomendado):

```bash
cd scripts/whatsapp_campaign
python3 -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## ⚙️ Configuração

### 1. Editar `config.py`

Abra o arquivo `config.py` e configure:

```python
# Token e Phone ID do WhatsApp Business
WHATSAPP_TOKEN = "seu_token_aqui"
WHATSAPP_PHONE_ID = "seu_phone_id_aqui"

# Nome do template (deve estar aprovado no Meta Business)
TEMPLATE_NAME = "campanha_fechamais"

# Arquivo CSV com os dados
CSV_FILE = "clientes.csv"
```

### 2. Configurar Mapeamento de Colunas

No `config.py`, ajuste o `CSV_COLUMN_MAPPING` de acordo com seu template:

```python
CSV_COLUMN_MAPPING = {
    "telefone": {"section": "phone", "position": 0},
    "nome": {"section": "header", "position": 0},
    "endereco": {"section": "body", "position": 0},
    "imobiliaria": {"section": "body", "position": 1},
    "link": {"section": "body", "position": 2},
    "whatsapp_contato": {"section": "body", "position": 3},
}
```

**Explicação:**
- `"telefone"` com `"section": "phone"` = coluna obrigatória com o número
- `"section": "header"` = variáveis do cabeçalho do template
- `"section": "body"` = variáveis do corpo do template
- `"position"` = ordem da variável (0, 1, 2, 3...)

### 3. Preparar Arquivo CSV

Crie um arquivo CSV com as colunas definidas no mapeamento. Exemplo:

```csv
telefone,nome,endereco,imobiliaria,link,whatsapp_contato
44999887766,João Silva,"Rua das Flores, 123",Imobiliária Central,https://exemplo.com,44 99119-3339
44988776655,Maria Santos,"Av. Brasil, 456",Imobiliária Premium,https://exemplo.com,44 99119-3339
```

**Dicas:**
- Use vírgulas para separar as colunas
- Coloque valores com vírgulas entre aspas duplas
- A coluna `telefone` é obrigatória
- O script adiciona automaticamente o código +55 se necessário

## 🚀 Uso

### Modo de Teste (Dry Run)

Primeiro, teste sem enviar mensagens reais:

```bash
python whatsapp_campaign.py
```

No `config.py`, certifique-se de que `DRY_RUN = True`.

### Modo de Produção

Quando estiver pronto para enviar:

1. Altere `DRY_RUN = False` no `config.py`
2. Execute:

```bash
python whatsapp_campaign.py
```

3. Confirme quando solicitado

## 📊 Saída do Script

Durante a execução, você verá:

```
============================================================
🚀 INICIANDO CAMPANHA DE WHATSAPP
============================================================

📋 Template:        campanha_fechamais
🌍 Idioma:          pt_BR
📁 Arquivo CSV:     clientes.csv
⏱️  Intervalo:       1.0s entre envios
🔍 Modo:            PRODUÇÃO

📊 Total de registros: 150

▶️  Iniciando envios...

Progresso: |████████████████████-----------| 75/150 (50.0%) ✓72 ✗3
```

Ao final:

```
============================================================
🏁 CAMPANHA FINALIZADA
============================================================
📅 Início:          10/02/2026 14:30:00
📅 Término:         10/02/2026 14:35:30
⏱️  Duração:         0:05:30
📊 Total:           150
✅ Sucessos:        147
❌ Erros:           3
📈 Taxa de Sucesso: 98.00%

⚠️  ERROS DETALHADOS:
  1. 44999887766: Número inválido
  2. 44988776655: Falha no envio
  3. 44977665544: Timeout

============================================================
📝 Log completo salvo em: whatsapp_campaign.log
============================================================
```

## 📝 Logs

Todos os eventos são registrados em `whatsapp_campaign.log`:

```
2026-02-10 14:30:15 - INFO - ✓ Mensagem enviada com sucesso para 5544999887766
2026-02-10 14:30:16 - ERROR - ✗ Erro HTTP 400 para 5544988776655: Invalid phone number
```

## 🔧 Configurações Avançadas

### Ajustar Intervalo entre Envios

Para evitar rate limiting da API:

```python
# config.py
SEND_INTERVAL = 2.0  # Aguarda 2 segundos entre cada envio
```

### Parar em Caso de Erro

Se quiser que o script pare ao encontrar um erro:

```python
# config.py
CONTINUE_ON_ERROR = False
```

### Ajustar Progresso no Terminal

Mostrar progresso a cada X mensagens:

```python
# config.py
PROGRESS_INTERVAL = 5  # Mostra a cada 5 mensagens
```

## 🔑 Obtendo Credenciais da API

1. Acesse [Meta Business Suite](https://business.facebook.com/)
2. Vá para WhatsApp > Configurações da API
3. Copie:
   - **Token de Acesso** → `WHATSAPP_TOKEN`
   - **ID do Telefone** → `WHATSAPP_PHONE_ID`

## 📱 Criando Templates no WhatsApp

1. Acesse Meta Business Suite
2. WhatsApp > Message Templates
3. Crie um novo template
4. Adicione variáveis usando `{{1}}`, `{{2}}`, etc.
5. Aguarde aprovação (geralmente 1-2 horas)
6. Use o nome do template em `TEMPLATE_NAME`

## ⚠️ Limitações e Boas Práticas

- ✅ Respeite os limites de envio da API do WhatsApp
- ✅ Use templates aprovados pelo Meta
- ✅ Teste sempre com `DRY_RUN = True` primeiro
- ✅ Mantenha um intervalo razoável entre envios (1-2s)
- ❌ Não envie spam ou mensagens não solicitadas
- ❌ Não exceda os limites de rate do WhatsApp Business

## 🐛 Solução de Problemas

### Erro: "Invalid access token"
- Verifique se `WHATSAPP_TOKEN` está correto
- O token pode ter expirado, gere um novo

### Erro: "Invalid phone number format"
- Certifique-se de que os números estão no formato correto
- O script adiciona +55 automaticamente

### Erro: "Template not found"
- Verifique se `TEMPLATE_NAME` está correto
- Certifique-se de que o template foi aprovado

### CSV não encontrado
- Verifique o caminho em `CSV_FILE`
- Certifique-se de que o arquivo está na mesma pasta

## 📞 Suporte

Em caso de dúvidas ou problemas, entre em contato com a equipe de desenvolvimento.

---

**Desenvolvido para Sistema Locarmais** | 2026
