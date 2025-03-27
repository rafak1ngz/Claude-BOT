import os
import logging
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
        
        # Listar modelos disponíveis
        models = genai.list_models()
        logger.info("Modelos encontrados:")
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                logger.info(f"Modelo disponível: {m.name}")
        
        # Selecionar modelo
        model = genai.GenerativeModel('gemini-pro')
        logger.info("Modelo Gemini configurado com sucesso!")
        return True
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
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def buscar_solucao_ia(modelo, problema):
    """
    Consulta modelo Gemini para encontrar solução técnica
    """
    try:
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        prompt = f"""
        Contexto: Suporte técnico de empilhadeira
        Modelo da Empilhadeira: {modelo}
        Problema Relatado: {problema}
        
        Forneça de forma clara e técnica:
        - Diagnóstico preliminar
        - Código da peça (se aplicável)
        - Procedimento de reparo
        - Possíveis causas do problema
        """
        
        logger.info(f"Enviando prompt para Gemini: {prompt}")
        resposta = model.generate_content(prompt)
        logger.info("Resposta do Gemini recebida")
        
        return resposta.text
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"Desculpe, não foi possível processar a solução. Erro: {str(e)}"

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    logger.info(f"Comando /start recebido de {message.from_user.username}")
    try:
        bot.reply_to(message, 
            "🚧 Assistente de Suporte Técnico de Empilhadeiras 🚧\n\n"
            "Envie o problema no formato: ModeloDaEmpilhadeira-DescriçãoProblema\n"
            "Exemplo: Hyster-RuidoNaTransmissão"
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
            bot.reply_to(message, "❌ Formato inválido. Use: Modelo-Problema")
            return
        
        modelo = partes[0].strip()
        problema = partes[1].strip()
        
        logger.info(f"Modelo extraído: {modelo}")
        logger.info(f"Problema extraído: {problema}")
        
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
            f"🔧 Solução para {modelo}:\n\n{solucao}\n\n"
            "Esta solução ajudou a resolver seu problema? (Sim/Não)")
    
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")

def main():
    # Configurações iniciais
    gemini_ok = configurar_gemini()
    mongodb_ok = configurar_mongodb()
    
    if not (gemini_ok and mongodb_ok):
        logger.critical("Falha em configurar serviços. Encerrando.")
        return
    
    logger.info("Inicializando bot de suporte técnico...")
    
    try:
        bot.polling(none_stop=True, timeout=90)
    except Exception as e:
        logger.critical(f"Erro fatal no polling: {e}", exc_info=True)

if __name__ == '__main__':
    main()