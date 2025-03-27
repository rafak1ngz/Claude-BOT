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

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

# Configuração do Gemini
def configurar_gemini():
    global model
    try:
        logger.info("Iniciando configuração do Gemini")
        genai.configure(api_key=GOOGLE_API_KEY)
        
        # Lista de modelos preferenciais em ordem
        modelos_preferidos = [
            'models/gemini-1.5-pro-latest',
            'models/gemini-1.5-flash-latest',
            'models/gemini-1.5-pro',
            'models/gemini-1.5-flash'
        ]
        
        models = genai.list_models()
        modelos_texto = [
            m.name for m in models 
            if 'generateContent' in m.supported_generation_methods 
            and ('pro' in m.name.lower() or 'flash' in m.name.lower())
        ]
        
        logger.info("Modelos de texto disponíveis:")
        for m in modelos_texto:
            logger.info(m)
        
        # Selecionar modelo prioritário
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
    """
    Consulta modelo Gemini para encontrar solução técnica
    """
    try:
        # Verificações preliminares
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        # Validações de entrada adicionais
        if not modelo or not problema:
            raise ValueError("Modelo e código de falha são obrigatórios")
        
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

        Apresente a resposta de forma técnica e clara, com linguagem de manual de manutenção.
        """
        
        logger.info(f"Enviando prompt para Gemini")
        
        # Timeout para evitar esperas longas
        try:
            resposta = model.generate_content(prompt, timeout=30)
        except Exception as timeout_error:
            logger.warning(f"Timeout na geração de conteúdo: {timeout_error}")
            return "Desculpe, a geração de conteúdo excedeu o tempo limite."
        
        logger.info("Resposta do Gemini recebida")
        return resposta.text
    
    except ValueError as ve:
        logger.error(f"Erro de validação: {ve}")
        return f"Erro de validação: {ve}"
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"Desculpe, não foi possível processar a solução técnica. Entre em contato com suporte técnico."

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    logger.info(f"Comando /start recebido de {message.from_user.username}")
    try:
        bot.reply_to(message, 
            "🚧 Assistente Técnico de Empilhadeiras 🚧\n\n"
            "Como funciono:\n"
            "• Envie o modelo da empilhadeira e o código de falha\n"
            "• Formato: ModeloEmpilhadeira-CódigoFalha\n"
            "• Exemplo: EGV-02A79\n\n"
            "Estou pronto para ajudar com diagnósticos técnicos!"
        )
    except Exception as e:
        logger.error(f"Erro no tratamento do /start: {e}", exc_info=True)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    logger.info(f"Mensagem recebida: {message.text}")
    
    try:
        # Validações de entrada
        if not message.text:
            bot.reply_to(message, "Por favor, envie uma mensagem válida.")
            return
        
        # Extrair informações
        partes = message.text.split('-')
        if len(partes) < 2:
            bot.reply_to(message, "❌ Formato inválido. Use: Modelo-CódigoFalha")
            return
        
        modelo = partes[0].strip()
        problema = partes[1].strip()
        
        logger.info(f"Modelo extraído: {modelo}")
        logger.info(f"Código de Falha: {problema}")
        
        # Buscar solução via IA
        solucao = buscar_solucao_ia(modelo, problema)
        
        # Salvar no banco de dados, se a conexão existir
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
        
        # Responder ao usuário
        bot.reply_to(message, 
            f"🔧 Diagnóstico para {modelo} - Código {problema}:\n\n{solucao}\n\n"
            "Estas informações ajudaram a resolver seu problema? (Sim/Não)")
    
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")

def start_bot():
    """
    Função para iniciar o bot com tratamento de exceções e reconexão
    """
    while True:
        try:
            logger.info("Iniciando polling do Telegram Bot")
            bot.remove_webhook()
            bot.polling(none_stop=True, timeout=90, long_polling_timeout=90)
        except Exception as e:
            logger.critical(f"Erro no polling do bot: {e}", exc_info=True)
            time.sleep(10)  # Aguarda 10 segundos antes de tentar novamente

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
    bot_thread.join()

if __name__ == '__main__':
    main()