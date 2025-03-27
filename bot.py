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

# Configuração do Gemini
def configurar_gemini():
    global model
    try:
        logger.info("Iniciando configuração do Gemini")
        genai.configure(api_key=GOOGLE_API_KEY)
        
        models = genai.list_models()
        modelos_texto = [
            m.name for m in models 
            if 'generateContent' in m.supported_generation_methods 
            and ('pro' in m.name.lower() or 'flash' in m.name.lower())
        ]
        
        logger.info("Modelos de texto disponíveis:")
        for m in modelos_texto:
            logger.info(m)
        
        # Priorizar modelos mais recentes
        modelos_preferidos = [
            'models/gemini-1.5-pro-latest',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-pro',
            'models/gemini-1.5-flash'
        ]
        
        modelo_selecionado = next((m for m in modelos_preferidos if m in modelos_texto), None)
        
        if modelo_selecionado:
            logger.info(f"Selecionando modelo: {modelo_selecionado}")
            model = genai.GenerativeModel(modelo_selecionado)
            logger.info("Modelo Gemini configurado com sucesso!")
            return True
        else:
            logger.error("Nenhum modelo de texto encontrado")
            return False
    except Exception as e:
        logger.error(f"Erro na configuração do Gemini: {e}", exc_info=True)
        return False

# Configuração do MongoDB
def configurar_mongodb():
    global manutencoes_collection
    try:
        logger.info("Iniciando conexão com MongoDB")
        mongo_client = MongoClient(MONGODB_URI, 
                                   server_api=ServerApi('1'), 
                                   tls=True)
        db = mongo_client['empilhadeiras_db']
        manutencoes_collection = db['manutencoes']
        logger.info("Conexão com MongoDB estabelecida com sucesso!")
        return True
    except Exception as e:
        logger.error(f"Erro na conexão com MongoDB: {e}", exc_info=True)
        return False

# Inicializar Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

def buscar_solucao_ia(modelo, problema):
    try:
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        prompt = f"""
        Contexto: Diagnóstico técnico de empilhadeira
        Modelo: {modelo}
        Código de Falha: {problema}

        Forneça um diagnóstico técnico detalhado:
        1. Análise do código de falha {problema}
        2. Possíveis causas da falha
        3. Procedimento de diagnóstico
        4. Passos para reparo ou manutenção
        5. Peças potencialmente envolvidas
        6. Recomendações de manutenção preventiva
        """
        
        logger.info(f"Enviando prompt para Gemini")
        resposta = model.generate_content(prompt, timeout=30)
        logger.info("Resposta do Gemini recebida")
        return resposta.text
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"Desculpe, não foi possível processar a solução técnica. Erro: {str(e)}"

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

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    logger.info(f"Mensagem recebida: {message.text}")
    
    try:
        if not message.text:
            bot.reply_to(message, "Por favor, envie uma mensagem válida.")
            return
        
        partes = message.text.split('-')
        if len(partes) < 2:
            bot.reply_to(message, "❌ Formato inválido. Use: Modelo-CódigoFalha")
            return
        
        modelo = partes[0].strip()
        problema = partes[1].strip()
        
        solucao = buscar_solucao_ia(modelo, problema)
        
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
        
        bot.reply_to(message, 
            f"🔧 Diagnóstico para {modelo} - Código {problema}:\n\n{solucao}\n\n"
            "Estas informações ajudaram a resolver seu problema? (Sim/Não)")
    
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