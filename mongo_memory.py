# MONGO_MEMORY.PY

import os
from pymongo import MongoClient
from fuzzywuzzy import fuzz

# --- Conexión ---
_client = None
_db = None
_collection = None

def _get_collection():
    """Obtiene (o crea) la conexión a MongoDB."""
    global _client, _db, _collection
    if _collection is not None:
        return _collection
    
    uri = os.getenv('MONGODB_URI')
    if not uri:
        print("⚠️ MONGODB_URI no existe en las variables de entorno.")
        return None
    
    try:
        # Se aumentó el timeout a 10s para Railway
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        
        # Nombres ÚNICOS para no mezclar con bases de datos de clientes/totoriales en la nube
        _db = _client['pomposo_bot']
        _collection = _db['memory']
        
        # Test conexión
        _client.admin.command('ping')
        print("✅ Conectado a MongoDB Atlas (Colección 100% aislada)")
        return _collection
    except Exception as e:
        print(f"⚠️ Error conectando a MongoDB: {e}")
        _collection = None
        return None


# --- Funciones de Memoria ESTRICTA de Base de Datos ---

def leer_memoria_completa() -> str:
    """Lee toda la memoria directamente de la base de datos."""
    col = _get_collection()
    if col is None:
        return "(Sin conexión a la base de datos)"
    
    try:
        entries = col.find({}, {'texto': 1}).sort('_id', 1)
        lineas = [doc['texto'] for doc in entries if 'texto' in doc]
        return '\n'.join(lineas)
    except Exception as e:
        print(f"Error leyendo MongoDB: {e}")
        return "(Error leyendo base de datos)"


def leer_memoria_lineas() -> list:
    """Lee la memoria como lista de líneas."""
    col = _get_collection()
    if col is None:
        return []
    
    try:
        entries = col.find({}, {'texto': 1}).sort('_id', 1)
        return [doc['texto'] for doc in entries if 'texto' in doc]
    except Exception as e:
        print(f"Error leyendo MongoDB: {e}")
        return []


def escribir_en_memoria(texto: str):
    """Agrega una línea DIRECTAMENTE a MongoDB (Sin TXT local)."""
    col = _get_collection()
    if col is None:
        print("❌ Eror: No se guardó (sin base de datos)")
        return
    
    try:
        col.insert_one({'texto': texto.strip()})
        print(f"💾 Guardado en MongoDB: {texto}")
    except Exception as e:
        print(f"Error escribiendo en MongoDB: {e}")


def reescribir_memoria_lineas(lineas: list):
    """Reescribe toda la base de datos con las líneas dadas."""
    col = _get_collection()
    if col is None:
        return
    
    try:
        col.delete_many({})
        if lineas:
            col.insert_many([{'texto': l.strip()} for l in lineas if l.strip()])
    except Exception as e:
        print(f"Error reescribiendo MongoDB: {e}")


def olvidar_por_texto(texto_a_buscar: str) -> str:
    """Busca y elimina el texto exacto o parecido directamente en MongoDB."""
    col = _get_collection()
    if col is None:
        return None
    
    try:
        entries = list(col.find({}, {'texto': 1}))
        mejor = None
        mejor_score = 0
        texto_lower = texto_a_buscar.lower()
        
        for doc in entries:
            # token_set_ratio es mucho mejor para coincidencias parciales y desordenadas
            score = fuzz.token_set_ratio(texto_lower, doc.get('texto', '').lower())
            if score > mejor_score:
                mejor_score = score
                mejor = doc
        
        # Umbral muy permisivo para olvidar ideas
        if mejor and mejor_score >= 50:
            col.delete_one({'_id': mejor['_id']})
            return mejor.get('texto')
        return None
    except Exception as e:
        print(f"Error olvidando en MongoDB: {e}")
        return None
