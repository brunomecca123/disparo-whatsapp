#!/usr/bin/env python3
"""
Script de Envio em Massa de Mensagens WhatsApp
Baseado na migration do Laravel para campanhas

Autor: Sistema Locarmais
Data: 2026-02-10
"""

import csv
import time
import logging
import sys
import mimetypes
from datetime import datetime
from typing import Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from colorama import Fore, Style, init

# Importar configurações
import config

# Inicializar colorama para cores no terminal
init(autoreset=True)


class WhatsAppCampaign:
    """Classe principal para gerenciar campanha de WhatsApp"""

    def __init__(self):
        self.setup_logging()
        self.success_count = 0
        self.error_count = 0
        self.errors = []
        self.total_records = 0
        self.api_url = f"https://graph.facebook.com/{config.API_VERSION}/{config.WHATSAPP_PHONE_ID}/messages"
        self._session = self._create_session()

    def setup_logging(self):
        """Configura o sistema de logging"""
        log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(config.LOG_FILE, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def _create_session(self):
        """Cria session com retry automático para uploads"""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        return session

    def normalize_phone_number(self, phone: str) -> str:
        """
        Normaliza o número de telefone
        Remove caracteres não numéricos e adiciona código do país (55) se necessário
        """
        # Remove todos os caracteres não numéricos
        phone = ''.join(filter(str.isdigit, phone))

        # Adiciona código do Brasil se não tiver
        if not phone.startswith('55'):
            phone = '55' + phone

        return phone

    def upload_media(self, file_path: str, media_type: str = "image") -> str:
        """
        Faz upload de um arquivo local para o Meta e retorna o media_id.
        Suporta imagens, documentos, áudio e vídeo.
        """
        import os

        # Validar existência do arquivo
        if not os.path.exists(file_path):
            print(f"{Fore.RED}✗ Arquivo não encontrado: {file_path}{Style.RESET_ALL}\n")
            sys.exit(1)

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # Determinar mime type
        mime_type, _ = mimetypes.guess_type(file_path)

        # Mapeamento de mime types por tipo de media
        mime_defaults = {
            "image": "image/jpeg",
            "video": "video/mp4",
            "document": "application/pdf",
            "audio": "audio/mpeg"
        }

        if not mime_type:
            mime_type = mime_defaults.get(media_type, "application/octet-stream")

        upload_url = f"https://graph.facebook.com/{config.API_VERSION}/{config.WHATSAPP_PHONE_ID}/media"
        headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}

        media_labels = {
            "image": "imagem",
            "video": "vídeo",
            "document": "documento",
            "audio": "áudio"
        }
        media_label = media_labels.get(media_type, media_type)
        print(f"\n{Fore.CYAN}⬆️  Fazendo upload do {media_label}: {file_path}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}   Tamanho: {file_size_mb:.1f}MB | Tipo MIME: {mime_type}{Style.RESET_ALL}")

        try:
            with open(file_path, "rb") as f:
                response = self._session.post(
                    upload_url,
                    headers=headers,
                    data={"messaging_product": "whatsapp"},
                    files={"file": (file_path, f, mime_type)},
                    timeout=600,
                )

            if response.status_code == 200:
                media_id = response.json().get("id", "")
                print(f"{Fore.GREEN}✓ Upload concluído. Media ID: {media_id}{Style.RESET_ALL}")
                config_vars = {
                    "image": "HEADER_IMAGE_ID",
                    "video": "HEADER_VIDEO_ID",
                    "document": "HEADER_DOCUMENT_ID",
                    "audio": "HEADER_AUDIO_ID"
                }
                config_var = config_vars.get(media_type, "HEADER_MEDIA_ID")
                print(f"{Fore.YELLOW}  Dica: salve este ID em {config_var} no config.py para evitar uploads futuros.{Style.RESET_ALL}\n")
                return media_id
            else:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("error", {}).get("message", response.text)
                error_code = error_data.get("error", {}).get("code", "")
                error_type = error_data.get("error", {}).get("type", "")

                print(f"{Fore.RED}✗ Falha no upload do {media_label}{Style.RESET_ALL}")
                print(f"{Fore.RED}   HTTP {response.status_code}{Style.RESET_ALL}")
                print(f"{Fore.RED}   Código: {error_code}{Style.RESET_ALL}")
                print(f"{Fore.RED}   Tipo: {error_type}{Style.RESET_ALL}")
                print(f"{Fore.RED}   Mensagem: {error_msg}{Style.RESET_ALL}")

                # Dicas comuns
                if error_code == 100 or "Invalid parameter" in error_msg:
                    print(f"{Fore.YELLOW}\n💡 Dicas para erro #100 (Invalid parameter):{Style.RESET_ALL}")
                    print(f"   • Verifique WHATSAPP_TOKEN (pode estar expirado)")
                    print(f"   • Verifique WHATSAPP_PHONE_ID")
                    print(f"   • Para vídeo: tente formato MP4 com codec H.264 + AAC")
                    print(f"   • Tamanho máximo: 100MB (seu arquivo: {file_size_mb:.1f}MB)")

                print()
                sys.exit(1)

        except Exception as e:
            print(f"{Fore.RED}✗ Erro na conexão: {str(e)}{Style.RESET_ALL}\n")
            sys.exit(1)

    def resolve_header_image(self) -> Optional[Dict]:
        """
        Retorna o parâmetro de imagem do header resolvendo a fonte configurada:
        1. HEADER_IMAGE_ID  (prioridade máxima — usa ID já existente)
        2. HEADER_IMAGE_PATH (faz upload do arquivo local)
        3. HEADER_IMAGE_URL  (URL pública)
        """
        image_id = getattr(config, "HEADER_IMAGE_ID", "").strip()
        if image_id:
            return {"type": "image", "image": {"id": image_id}}

        image_path = getattr(config, "HEADER_IMAGE_PATH", "").strip()
        if image_path:
            media_id = self.upload_media(image_path, media_type="image")
            return {"type": "image", "image": {"id": media_id}}

        image_url = getattr(config, "HEADER_IMAGE_URL", "").strip()
        if image_url:
            return {"type": "image", "image": {"link": image_url}}

        print(f"{Fore.RED}✗ HEADER_TYPE é 'image' mas nenhuma fonte de imagem foi configurada.{Style.RESET_ALL}\n")
        sys.exit(1)

    def resolve_header_video(self) -> Optional[Dict]:
        """
        Retorna o parâmetro de vídeo do header resolvendo a fonte configurada:
        1. HEADER_VIDEO_ID  (prioridade máxima — usa ID já existente)
        2. HEADER_VIDEO_PATH (faz upload do arquivo local)
        3. HEADER_VIDEO_URL  (URL pública)
        """
        video_id = getattr(config, "HEADER_VIDEO_ID", "").strip()
        if video_id:
            return {"type": "video", "video": {"id": video_id}}

        video_path = getattr(config, "HEADER_VIDEO_PATH", "").strip()
        if video_path:
            media_id = self.upload_media(video_path, media_type="video")
            return {"type": "video", "video": {"id": media_id}}

        video_url = getattr(config, "HEADER_VIDEO_URL", "").strip()
        if video_url:
            return {"type": "video", "video": {"link": video_url}}

        print(f"{Fore.RED}✗ HEADER_TYPE é 'video' mas nenhuma fonte de vídeo foi configurada.{Style.RESET_ALL}\n")
        sys.exit(1)

    def resolve_header_document(self) -> Optional[Dict]:
        """
        Retorna o parâmetro de documento do header resolvendo a fonte configurada:
        1. HEADER_DOCUMENT_ID  (prioridade máxima — usa ID já existente)
        2. HEADER_DOCUMENT_PATH (faz upload do arquivo local)
        3. HEADER_DOCUMENT_URL  (URL pública)
        """
        doc_id = getattr(config, "HEADER_DOCUMENT_ID", "").strip()
        if doc_id:
            return {"type": "document", "document": {"id": doc_id}}

        doc_path = getattr(config, "HEADER_DOCUMENT_PATH", "").strip()
        if doc_path:
            media_id = self.upload_media(doc_path, media_type="document")
            return {"type": "document", "document": {"id": media_id}}

        doc_url = getattr(config, "HEADER_DOCUMENT_URL", "").strip()
        if doc_url:
            return {"type": "document", "document": {"link": doc_url}}

        print(f"{Fore.RED}✗ HEADER_TYPE é 'document' mas nenhuma fonte de documento foi configurada.{Style.RESET_ALL}\n")
        sys.exit(1)

    def build_template_components(self, row_data: Dict[str, str]) -> List[Dict]:
        """
        Constrói os componentes do template baseado no mapeamento do CSV
        """
        components = []
        header_params = []
        body_params = []

        # Organizar parâmetros por seção e posição
        for column_name, mapping in config.CSV_COLUMN_MAPPING.items():
            if mapping["section"] == "phone":
                continue  # Telefone não vai para o template

            value = row_data.get(column_name, "")

            if mapping["section"] == "header":
                header_params.append((mapping["position"], value))
            elif mapping["section"] == "body":
                body_params.append((mapping["position"], value))

        # Montar header conforme o tipo configurado
        header_type = getattr(config, "HEADER_TYPE", "text")

        if header_type == "image":
            components.append({
                "type": "header",
                "parameters": [self._header_image_param]
            })
        elif header_type == "video":
            components.append({
                "type": "header",
                "parameters": [self._header_video_param]
            })
        elif header_type == "document":
            components.append({
                "type": "header",
                "parameters": [self._header_document_param]
            })
        elif header_type == "text" and header_params:
            header_params.sort(key=lambda x: x[0])
            components.append({
                "type": "header",
                "parameters": [
                    {"type": "text", "text": param[1]}
                    for param in header_params
                ]
            })

        if body_params:
            body_params.sort(key=lambda x: x[0])
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": param[1]}
                    for param in body_params
                ]
            })

        return components

    def send_whatsapp_message(self, phone: str, components: List[Dict]) -> bool:
        """
        Envia mensagem via API do WhatsApp
        Retorna True se sucesso, False se erro
        """
        normalized_phone = self.normalize_phone_number(phone)

        payload = {
            "messaging_product": "whatsapp",
            "to": normalized_phone,
            "type": "template",
            "template": {
                "name": config.TEMPLATE_NAME,
                "language": {
                    "code": config.LANGUAGE_CODE,
                    "policy": "deterministic"
                },
                "components": components
            }
        }

        headers = {
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        if config.DRY_RUN:
            self.logger.info(f"[DRY RUN] Enviaria mensagem para {normalized_phone}")
            import json
            print(f"\n{Fore.CYAN}[DRY RUN] Payload que seria enviado:{Style.RESET_ALL}")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print()
            return True

        try:
            response = self._session.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                response_data = response.json()
                message_status = response_data.get('messages', [{}])[0].get('message_status')
                message_id = response_data.get('messages', [{}])[0].get('id', 'N/A')

                if message_status == 'accepted':
                    self.logger.info(f"✓ Mensagem enviada com sucesso para {normalized_phone} (ID: {message_id})")
                    print(f"{Fore.GREEN}✓ Mensagem aceita para {normalized_phone}{Style.RESET_ALL}")
                    return True
                else:
                    self.logger.error(f"✗ Mensagem status: {message_status} para {normalized_phone}")
                    print(f"{Fore.YELLOW}⚠ Mensagem para {normalized_phone}: {message_status}{Style.RESET_ALL}")
                    return False
            else:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get('error', {}).get('message', 'Erro desconhecido')
                error_code = error_data.get('error', {}).get('code', '')

                self.logger.error(f"✗ HTTP {response.status_code} para {normalized_phone}: {error_msg}")
                print(f"{Fore.RED}✗ Erro HTTP {response.status_code} para {normalized_phone}{Style.RESET_ALL}")
                print(f"{Fore.RED}   Código: {error_code} | {error_msg}{Style.RESET_ALL}")

                # Dicas comuns
                if error_code == 131000 or "template" in error_msg.lower():
                    print(f"{Fore.YELLOW}💡 Template pode não estar aprovado ou não existe{Style.RESET_ALL}")
                elif error_code == 100:
                    print(f"{Fore.YELLOW}💡 Parâmetro inválido - verifique template e componentes{Style.RESET_ALL}")

                return False

        except requests.exceptions.RequestException as e:
            self.logger.error(f"✗ Erro de conexão ao enviar para {normalized_phone}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"✗ Erro inesperado ao enviar para {normalized_phone}: {str(e)}")
            return False

    def print_progress(self, processed: int, total: int):
        """Imprime barra de progresso no terminal"""
        percentage = (processed / total) * 100
        bar_length = 40
        filled_length = int(bar_length * processed // total)
        bar = '█' * filled_length + '-' * (bar_length - filled_length)

        print(f'\r{Fore.CYAN}Progresso: |{bar}| {processed}/{total} ({percentage:.1f}%) '
              f'{Fore.GREEN}✓{self.success_count} {Fore.RED}✗{self.error_count}',
              end='', flush=True)

    def print_summary(self, start_time: datetime, end_time: datetime):
        """Imprime resumo final da campanha"""
        duration = end_time - start_time
        success_rate = (self.success_count / self.total_records * 100) if self.total_records > 0 else 0

        print("\n\n" + "=" * 60)
        print(f"{Fore.GREEN}{Style.BRIGHT}🏁 CAMPANHA FINALIZADA{Style.RESET_ALL}")
        print("=" * 60)
        print(f"📅 Início:          {start_time.strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"📅 Término:         {end_time.strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"⏱️  Duração:         {duration}")
        print(f"📊 Total:           {self.total_records}")
        print(f"{Fore.GREEN}✅ Sucessos:        {self.success_count}{Style.RESET_ALL}")
        print(f"{Fore.RED}❌ Erros:           {self.error_count}{Style.RESET_ALL}")
        print(f"📈 Taxa de Sucesso: {success_rate:.2f}%")

        if self.errors:
            print(f"\n{Fore.YELLOW}⚠️  ERROS DETALHADOS:{Style.RESET_ALL}")
            for i, error in enumerate(self.errors[:20], 1):
                print(f"  {i}. {error['phone']}: {error['error']}")
            if len(self.errors) > 20:
                print(f"  ... e mais {len(self.errors) - 20} erros")

        print("=" * 60)
        print(f"📝 Log completo salvo em: {config.LOG_FILE}")
        print("=" * 60 + "\n")

    def validate_config(self) -> bool:
        """Valida as configurações antes de iniciar"""
        errors = []

        if config.WHATSAPP_TOKEN == "seu_token_aqui":
            errors.append("❌ WHATSAPP_TOKEN não configurado em config.py")

        if config.WHATSAPP_PHONE_ID == "seu_phone_id_aqui":
            errors.append("❌ WHATSAPP_PHONE_ID não configurado em config.py")

        if not config.CSV_FILE:
            errors.append("❌ CSV_FILE não especificado em config.py")

        # Verificar se existe mapeamento para telefone
        phone_mapping = [m for m in config.CSV_COLUMN_MAPPING.values() if m.get("section") == "phone"]
        if not phone_mapping:
            errors.append("❌ Nenhuma coluna mapeada como 'phone' em CSV_COLUMN_MAPPING")

        # Validar configurações específicas do header type
        header_type = getattr(config, "HEADER_TYPE", "text")
        if header_type == "document":
            doc_id = getattr(config, "HEADER_DOCUMENT_ID", "").strip()
            doc_path = getattr(config, "HEADER_DOCUMENT_PATH", "").strip()
            doc_url = getattr(config, "HEADER_DOCUMENT_URL", "").strip()
            if not doc_id and not doc_path and not doc_url:
                errors.append("❌ HEADER_TYPE é 'document' mas nenhuma fonte foi configurada (HEADER_DOCUMENT_ID, HEADER_DOCUMENT_PATH ou HEADER_DOCUMENT_URL)")
        elif header_type == "video":
            video_id = getattr(config, "HEADER_VIDEO_ID", "").strip()
            video_path = getattr(config, "HEADER_VIDEO_PATH", "").strip()
            video_url = getattr(config, "HEADER_VIDEO_URL", "").strip()
            if not video_id and not video_path and not video_url:
                errors.append("❌ HEADER_TYPE é 'video' mas nenhuma fonte foi configurada (HEADER_VIDEO_ID, HEADER_VIDEO_PATH ou HEADER_VIDEO_URL)")
        elif header_type == "image":
            image_id = getattr(config, "HEADER_IMAGE_ID", "").strip()
            image_path = getattr(config, "HEADER_IMAGE_PATH", "").strip()
            image_url = getattr(config, "HEADER_IMAGE_URL", "").strip()
            if not image_id and not image_path and not image_url:
                errors.append("❌ HEADER_TYPE é 'image' mas nenhuma fonte foi configurada (HEADER_IMAGE_ID, HEADER_IMAGE_PATH ou HEADER_IMAGE_URL)")

        if errors:
            print(f"\n{Fore.RED}{Style.BRIGHT}⚠️  ERROS DE CONFIGURAÇÃO:{Style.RESET_ALL}\n")
            for error in errors:
                print(f"  {error}")
            print(f"\n{Fore.YELLOW}→ Edite o arquivo config.py antes de continuar{Style.RESET_ALL}\n")
            return False

        return True

    def run(self):
        """Executa a campanha de envio"""
        print(f"\n{Fore.CYAN}{Style.BRIGHT}{'='*60}")
        print(f"🚀 INICIANDO CAMPANHA DE WHATSAPP")
        print(f"{'='*60}{Style.RESET_ALL}\n")

        # Validar configurações
        if not self.validate_config():
            sys.exit(1)

        # Exibir configurações
        print(f"📋 Template:        {Fore.YELLOW}{config.TEMPLATE_NAME}{Style.RESET_ALL}")
        print(f"🌍 Idioma:          {config.LANGUAGE_CODE}")
        print(f"📁 Arquivo CSV:     {config.CSV_FILE}")
        print(f"⏱️  Intervalo:       {config.SEND_INTERVAL}s entre envios")
        print(f"🔍 Modo:            {Fore.YELLOW if config.DRY_RUN else Fore.GREEN}{'DRY RUN (teste)' if config.DRY_RUN else 'PRODUÇÃO'}{Style.RESET_ALL}")

        # Ler arquivo CSV
        try:
            with open(config.CSV_FILE, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=config.CSV_DELIMITER)
                records = list(reader)
                self.total_records = len(records)
        except FileNotFoundError:
            print(f"\n{Fore.RED}❌ Arquivo CSV não encontrado: {config.CSV_FILE}{Style.RESET_ALL}\n")
            sys.exit(1)
        except Exception as e:
            print(f"\n{Fore.RED}❌ Erro ao ler CSV: {str(e)}{Style.RESET_ALL}\n")
            sys.exit(1)

        if self.total_records == 0:
            print(f"\n{Fore.RED}❌ Nenhum registro encontrado no CSV{Style.RESET_ALL}\n")
            sys.exit(1)

        print(f"\n📊 Total de registros: {Fore.GREEN}{self.total_records}{Style.RESET_ALL}\n")

        # Confirmação para produção
        if not config.DRY_RUN:
            response = input(f"{Fore.YELLOW}⚠️  Deseja continuar com o envio? (s/N): {Style.RESET_ALL}").strip().lower()
            if response != 's':
                print(f"\n{Fore.YELLOW}❌ Operação cancelada pelo usuário{Style.RESET_ALL}\n")
                sys.exit(0)

        # Resolver imagem, vídeo ou documento do header uma única vez antes do loop
        header_type = getattr(config, "HEADER_TYPE", "text")
        if header_type == "image":
            self._header_image_param = self.resolve_header_image()
        elif header_type == "video":
            self._header_video_param = self.resolve_header_video()
        elif header_type == "document":
            self._header_document_param = self.resolve_header_document()

        start_time = datetime.now()
        print(f"\n{Fore.GREEN}▶️  Iniciando envios...{Style.RESET_ALL}\n")

        # Obter nome da coluna do telefone
        phone_column = None
        for column, mapping in config.CSV_COLUMN_MAPPING.items():
            if mapping.get("section") == "phone":
                phone_column = column
                break

        # Processar cada registro
        for idx, row in enumerate(records, 1):
            phone = row.get(phone_column, "").strip()

            if not phone:
                self.logger.warning(f"Linha {idx}: telefone vazio, pulando...")
                self.error_count += 1
                self.errors.append({"phone": "N/A", "error": "Telefone vazio"})
                continue

            try:
                # Construir componentes do template
                components = self.build_template_components(row)

                # Enviar mensagem
                success = self.send_whatsapp_message(phone, components)

                if success:
                    self.success_count += 1
                else:
                    self.error_count += 1
                    self.errors.append({"phone": phone, "error": "Falha no envio"})

                    if not config.CONTINUE_ON_ERROR:
                        print(f"\n\n{Fore.RED}❌ Parando execução devido a erro{Style.RESET_ALL}\n")
                        break

            except Exception as e:
                self.error_count += 1
                self.errors.append({"phone": phone, "error": str(e)})
                self.logger.error(f"Erro ao processar linha {idx}: {str(e)}")

                if not config.CONTINUE_ON_ERROR:
                    print(f"\n\n{Fore.RED}❌ Parando execução devido a erro{Style.RESET_ALL}\n")
                    break

            # Atualizar progresso
            self.print_progress(idx, self.total_records)

            # Aguardar intervalo entre envios
            if idx < self.total_records:
                time.sleep(config.SEND_INTERVAL)

        end_time = datetime.now()

        # Imprimir resumo final
        self.print_summary(start_time, end_time)


def main():
    """Função principal"""
    try:
        campaign = WhatsAppCampaign()
        campaign.run()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}⚠️  Operação interrompida pelo usuário{Style.RESET_ALL}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n{Fore.RED}❌ Erro fatal: {str(e)}{Style.RESET_ALL}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
