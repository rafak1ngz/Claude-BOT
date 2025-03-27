import os
import logging
import time
import threading
import telebot
import google.generativeai as genai
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
from datetime import datetime
import sys
import re
import html

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
MONGODB_URI = os.getenv('MONGODB_URI')

# Variáveis globais
model = None
manutencoes_collection = None
bot_running = threading.Event()
user_feedback_state = {}

# Função de sanitização de HTML melhorada
def sanitizar_html(texto):
    try:
        # Remover tags HTML não desejadas
        texto = re.sub(r'<html.*?>', '', texto, flags=re.DOTALL)
        texto = re.sub(r'</html>', '', texto)
        texto = re.sub(r'<body.*?>', '', texto, flags=re.DOTALL)
        texto = re.sub(r'</body>', '', texto)
        
        # Lista de tags permitidas
        tags_permitidas = ['b', 'i', 'u', 'code', 'pre']
        
        # Dicionário para mapeamento de tags
        mapeamento_tags = {
            '<p>': '\n',
            '</p>': '\n',
            '<br>': '\n',
            '<br/>': '\n',
            '<br />': '\n'
        }
        
        # Substituir tags de parágrafo e quebra de linha
        for tag_original, tag_substituicao in mapeamento_tags.items():
            texto = texto.replace(tag_original, tag_substituicao)
        
        # Remover tags li e converter para linhas com bullet
        texto = re.sub(r'<li>', '• ', texto)
        texto = re.sub(r'</li>', '\n', texto)
        
        # Remover outras tags não permitidas
        for tag in re.findall(r'</?[a-zA-Z]+.*?>', texto):
            if not any(f'<{permitida}' in tag or f'</{permitida}' in tag for permitida in tags_permitidas):
                texto = texto.replace(tag, '')
        
        # Primeiro, escape de caracteres especiais
        texto = html.escape(texto)
        
        # Restaurar tags permitidas
        for tag in tags_permitidas:
            texto = texto.replace(f'&lt;{tag}&gt;', f'<{tag}>')
            texto = texto.replace(f'&lt;/{tag}&gt;', f'</{tag}>')
        
        # Remover espaços extras e linhas em branco
        texto = re.sub(r'\n\s*\n', '\n\n', texto).strip()
        
        return texto
    except Exception as e:
        logger.error(f"Erro na sanitização HTML: {e}")
        return "Erro ao processar resposta técnica."

# Configuração do Gemini
def configurar_gemini():
    global model
    try:
        logger.info("Iniciando configuração do Gemini")
        genai.configure(api_key=GOOGLE_API_KEY)
        
        # Lista de modelos recomendados para substituição
        modelos_preferidos = [
            'gemini-1.5-pro-latest',
            'gemini-1.5-pro',
            'gemini-1.5-flash-latest', 
            'gemini-1.5-flash',
            'gemini-pro'
        ]
        
        modelo_funcionando = None
        
        for nome_modelo in modelos_preferidos:
            try:
                logger.info(f"Tentando configurar modelo: {nome_modelo}")
                model = genai.GenerativeModel(nome_modelo)
                
                # Teste rápido de geração de conteúdo
                teste_resposta = model.generate_content("Sistema de empilhadeira")
                
                logger.info(f"Modelo {nome_modelo} configurado com sucesso!")
                modelo_funcionando = nome_modelo
                break
            except Exception as e:
                logger.warning(f"Falha ao configurar {nome_modelo}: {e}")
        
        if modelo_funcionando:
            return True
        else:
            logger.error("Nenhum modelo de texto encontrado ou funcional")
            return False
    
    except Exception as e:
        logger.error(f"Erro crítico na configuração do Gemini: {e}", exc_info=True)
        return False

# Configuração do MongoDB
def configurar_mongodb():
    global manutencoes_collection
    try:
        logger.info("Iniciando conexão com MongoDB")
        # Configurações SSL mais flexíveis
        mongo_client = MongoClient(
            MONGODB_URI, 
            server_api=ServerApi('1'), 
            tls=True,
            tlsAllowInvalidCertificates=True,  # Permite certificados inválidos
            socketTimeoutMS=30000,  # Timeout de 30 segundos
            connectTimeoutMS=30000,
            serverSelectionTimeoutMS=30000,
            waitQueueTimeoutMS=30000
        )
        
        db = mongo_client['empilhadeiras_db']
        manutencoes_collection = db['manutencoes']
        logger.info("Conexão com MongoDB estabelecida com sucesso!")
        return True
    except Exception as e:
        logger.error(f"Erro na conexão com MongoDB: {e}", exc_info=True)
        return False

# Inicializar Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

def buscar_solucao_ia(modelo, problema):
    try:
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        prompt = f"""
        // Informações para guardar
        Modelo: {modelo}
        Código de Falha: {problema}

        // Objetivo
        Gere um diagnóstico técnico CURTO e DIRETO em HTML
        
        //Modelo a ser respondido
        🔧 Diagnóstico para {modelo} - Código {problema}:
        1. Análise do código de falha {problema}
        2. Possíveis causas da falha
        3. Procedimento de diagnóstico
        4. Passos para reparo ou manutenção
        5. Peças potencialmente envolvidas com seus códigos

        // Regras de formatação HTML
        • Use <b>negrito</b> para títulos
        • Use <i>itálico</i> para ênfases
        • Utilize <br> para quebras de linha
        • Crie listas com • no início de cada item
        • Seja técnico e direto
        • Não use tags HTML complexas ou DOCTYPE
        • Use apenas as listadas aqui
        """
        
        logger.info(f"Enviando prompt para Gemini")
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
        
        resposta = model.generate_content(
            prompt, 
            safety_settings=safety_settings,
            generation_config={
                "max_output_tokens": 2048,
                "temperature": 0.5,
                "top_p": 1
            }
        )
        
        # Sanitizar a resposta HTML
        texto_resposta = sanitizar_html(resposta.text)
        
        # Adicionar emoji para dar mais personalidade
        texto_formatado = f"🔧 Diagnóstico para {modelo} 🚨\n\n{texto_resposta}"
        
        logger.info("Resposta do Gemini recebida")
        return texto_formatado
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"🚫 Ops! Não consegui processar o diagnóstico. Erro: {str(e)} 😓"

def dividir_mensagem(texto, max_length=4000):
    # Sanitize the text first
    texto_sanitizado = sanitizar_html(texto)
    
    paragrafos = texto_sanitizado.split('\n')
    mensagens = []
    mensagem_atual = ""
    
    for paragrafo in paragrafos:
        if len(mensagem_atual) + len(paragrafo) > max_length:
            mensagens.append(mensagem_atual.strip())
            mensagem_atual = ""
        mensagem_atual += paragrafo + '\n'
    
    if mensagem_atual:
        mensagens.append(mensagem_atual.strip())
    
    return mensagens

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    logger.info(f"Comando /start recebido de {message.from_user.username}")
    bot.reply_to(message, 
        "🚧 Assistente Técnico de Empilhadeiras 🚧\n\n"
        "Como funciono:\n"
        "• Envie o modelo da empilhadeira e o código de falha\n"
        "• Formato: ModeloEmpilhadeira-CódigoFalha\n"
        "• Exemplo: EGV-02A79\n\n"
        "Estou pronto para ajudar com diagnósticos técnicos!"
    )

@bot.message_handler(func=lambda message: message.text.lower() in ['sim', 'não'])
def handle_feedback(message):
    user_id = message.from_user.id
    
    if user_id in user_feedback_state:
        if message.text.lower() == 'sim':
            bot.reply_to(message, "<b>🎉 Ótimo! Fico feliz em ter ajudado.</b>")
        else:
            bot.reply_to(message, "<b>😔 Lamento não ter resolvido completamente. Posso tentar ajudar novamente.</b>")
        
        # Limpar estado de feedback
        del user_feedback_state[user_id]
    else:
        bot.reply_to(message, "❌ Envie primeiro o modelo da empilhadeira.")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    logger.info(f"Mensagem recebida: {message.text}")
    
    try:
        if not message.text:
            bot.reply_to(message, "Por favor, envie uma mensagem válida.")
            return
        
        # Ignorar mensagens de feedback se não seguirem o formato
        if message.text.lower() in ['sim', 'não']:
            bot.reply_to(message, "❌ Primeiro informe o modelo da empilhadeira.")
            return
        
        partes = message.text.split('-')
        if len(partes) < 2:
            bot.reply_to(message, "❌ Formato inválido. Use: Modelo-CódigoFalha")
            return
        
        modelo = partes[0].strip()
        problema = partes[1].strip()
        
        solucao = buscar_solucao_ia(modelo, problema)
        
        # Dividir mensagem longa em partes
        mensagens = dividir_mensagem(solucao)
        
        # Enviar primeira mensagem
        primeira_mensagem = f"🔧 Diagnóstico para {modelo} - Código {problema}:\n\n{mensagens[0]}"
        bot.reply_to(message, primeira_mensagem)
        
        # Enviar mensagens subsequentes
        for msg_adicional in mensagens[1:]:
            bot.send_message(message.chat.id, msg_adicional)
        
        if manutencoes_collection is not None:
            try:
                registro = {
                    'modelo': modelo,
                    'problema': problema,
                    'solucao': solucao,
                    'data': datetime.now()
                }
                manutencoes_collection.insert_one(registro)
                logger.info("Registro salvo no MongoDB")
            except Exception as db_error:
                logger.error(f"Erro ao salvar no banco de dados: {db_error}")
        
        # Registrar estado de feedback para este usuário
        user_feedback_state[message.from_user.id] = True
        
        # Mensagem final de feedback
        bot.send_message(message.chat.id, 
            "<b>Estas informações ajudaram a resolver seu problema?</b> Responda com <i>Sim</i> ou <i>Não</i>")
    
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")

def start_bot():
    tentativas = 0
    max_tentativas = 5
    while not bot_running.is_set() and tentativas < max_tentativas:
        try:
            logger.info(f"Tentativa {tentativas + 1} de iniciar o bot")
            bot.remove_webhook()
            
            # Adicionar polling com parâmetros mais robustos
            bot.polling(
                none_stop=True, 
                timeout=90, 
                long_polling_timeout=90,
                skip_pending=True  # Ignorar updates pendentes
            )
            
            bot_running.set()
        except telebot.apihelper.ApiException as e:
            logger.error(f"Erro de API do Telegram: {e}")
            if e.result.status_code == 409:
                logger.warning("Conflito de sessão detectado. Aguardando e tentando novamente...")
                time.sleep(10)  # Aguardar antes de tentar novamente
            tentativas += 1
        except Exception as e:
            logger.critical(f"Erro no polling do bot: {e}", exc_info=True)
            time.sleep(10)
            tentativas += 1
    
    if tentativas >= max_tentativas:
        logger.critical("Falha ao iniciar o bot após múltiplas tentativas")
        sys.exit(1)

def main():
    # Configurações iniciais
    gemini_ok = configurar_gemini()
    mongodb_ok = configurar_mongodb()
    
    if not (gemini_ok and mongodb_ok):
        logger.critical("Falha em configurar serviços. Encerrando.")
        return
    
    logger.info("Inicializando bot de suporte técnico...")
    
    # Inicia o bot em uma thread separada
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    # Manter o programa principal rodando
    try:
        bot_thread.join()
    except KeyboardInterrupt:
        logger.info("Encerrando bot...")
        bot_running.set()
        bot_thread.join()

if __name__ == '__main__':
    main()