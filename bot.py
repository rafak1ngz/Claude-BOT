import os
import telebot
import openai
import pymongo
from dotenv import load_dotenv
from datetime import datetime

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
MONGODB_URI = os.getenv('MONGODB_URI')

# Inicializar serviços
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
openai.api_key = OPENAI_API_KEY
mongo_client = pymongo.MongoClient(MONGODB_URI)
db = mongo_client['empilhadeiras_db']
manutencoes_collection = db['manutencoes']

def buscar_solucao_ia(modelo, problema):
    """
    Consulta modelo de IA para encontrar solução
    """
    prompt = f"""
    Contexto: Suporte técnico de empilhadeira
    Modelo: {modelo}
    Problema: {problema}
    
    Forneça:
    - Código da peça (se aplicável)
    - Procedimento de reparo
    - Possíveis causas
    """
    
    resposta = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": prompt}]
    )
    
    return resposta.choices[0].message.content

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    bot.reply_to(message, 
        "Olá! Sou o assistente de suporte técnico para empilhadeiras. " 
        "Envie o modelo e o problema que enfrentou."
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        # Extrair informações
        texto = message.text
        
        # Lógica simples de extração (pode ser melhorada)
        partes = texto.split('-')
        if len(partes) < 2:
            bot.reply_to(message, "Por favor, use o formato: Modelo-Problema")
            return
        
        modelo = partes[0].strip()
        problema = partes[1].strip()
        
        # Buscar solução via IA
        solucao = buscar_solucao_ia(modelo, problema)
        
        # Salvar no banco de dados
        registro = {
            'modelo': modelo,
            'problema': problema,
            'solucao': solucao,
            'data': datetime.now()
        }
        manutencoes_collection.insert_one(registro)
        
        # Responder ao usuário
        bot.reply_to(message, f"Solução encontrada:\n{solucao}\n\n"
                     "Esta solução resolveu seu problema? (Sim/Não)")
    
    except Exception as e:
        bot.reply_to(message, f"Erro ao processar: {str(e)}")

# Configuração para Railway
if __name__ == '__main__':
    bot.polling()