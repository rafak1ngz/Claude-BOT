import os
import telebot
import google.generativeai as genai
import pymongo
from dotenv import load_dotenv
from datetime import datetime
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
MONGODB_URI = os.getenv('MONGODB_URI')

# Inicializar serviços
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Configuração do Gemini
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # Listar TODOS os modelos e suas informações
    print("Modelos disponíveis:")
    models = genai.list_models()
    for m in models:
        print(f"Nome: {m.name}")
        print(f"Descrição: {m.description}")
        print(f"Métodos suportados: {m.supported_generation_methods}")
        print("---")
    
    # Tentar usar um modelo genérico
    model = genai.GenerativeModel('gemini-pro')
    print("Modelo Gemini configurado com sucesso!")
except Exception as e:
    print(f"Erro COMPLETO ao configurar Gemini: {e}")
    print(f"Tipo de erro: {type(e)}")
    import traceback
    traceback.print_exc()

def buscar_solucao_ia(modelo, problema):
    """
    Consulta modelo Gemini para encontrar solução
    """
    try:
        prompt = f"""
        Contexto: Suporte técnico de empilhadeira
        Modelo: {modelo}
        Problema: {problema}
        
        Forneça:
        - Código da peça (se aplicável)
        - Procedimento de reparo
        - Possíveis causas
        """
        
        print(f"Prompt enviado ao Gemini: {prompt}")
        resposta = model.generate_content(prompt)
        print(f"Resposta do Gemini: {resposta.text}")
        return resposta.text
    
    except Exception as e:
        print(f"Erro detalhado na consulta de IA: {e}")
        print(f"Tipo de erro: {type(e)}")
        import traceback
        traceback.print_exc()
        return f"Erro na consulta de IA: {str(e)}"

# Resto do código permanece o mesmo

# Variável global para verificação
manutencoes_collection = None

# Configuração segura do MongoDB
try:
    mongo_client = MongoClient(MONGODB_URI, 
                               server_api=ServerApi('1'), 
                               tls=True, 
                               tlsAllowInvalidCertificates=True)
    db = mongo_client['empilhadeiras_db']
    manutencoes_collection = db['manutencoes']
    print("Conexão com MongoDB estabelecida com sucesso!")
except Exception as e:
    print(f"Erro na conexão com MongoDB: {e}")

def buscar_solucao_ia(modelo, problema):
    """
    Consulta modelo Gemini para encontrar solução
    """
    try:
        prompt = f"""
        Contexto: Suporte técnico de empilhadeira
        Modelo: {modelo}
        Problema: {problema}
        
        Forneça:
        - Código da peça (se aplicável)
        - Procedimento de reparo
        - Possíveis causas
        """
        
        print(f"Prompt enviado ao Gemini: {prompt}")
        resposta = model.generate_content(prompt)
        print(f"Resposta do Gemini: {resposta.text}")
        return resposta.text
    
    except Exception as e:
        print(f"Erro detalhado na consulta de IA: {e}")
        return f"Erro na consulta de IA: {str(e)}"

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    print(f"Comando /start recebido de {message.from_user.username}")
    try:
        bot.reply_to(message, 
            "Olá! Sou o assistente de suporte técnico para empilhadeiras. " 
            "Envie o modelo e o problema que enfrentou no formato: Modelo-Problema"
        )
    except Exception as e:
        print(f"Erro no tratamento do /start: {e}")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    print(f"Mensagem recebida: {message.text}")
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
        
        print(f"Modelo extraído: {modelo}")
        print(f"Problema extraído: {problema}")
        
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
            except Exception as db_error:
                print(f"Erro ao salvar no banco de dados: {db_error}")
        
        # Responder ao usuário
        bot.reply_to(message, f"Solução encontrada:\n{solucao}\n\n"
                     "Esta solução resolveu seu problema? (Sim/Não)")
    
    except Exception as e:
        print(f"Erro detalhado ao processar: {e}")
        bot.reply_to(message, f"Erro ao processar: {str(e)}")

# Configuração para Railway
if __name__ == '__main__':
    print("Bot iniciado...")
    try:
        bot.polling(none_stop=True, timeout=90)
    except Exception as e:
        print(f"Erro fatal no polling: {e}")