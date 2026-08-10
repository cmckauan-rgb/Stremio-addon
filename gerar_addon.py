import os
import re
import json
import requests
from urllib.parse import quote

# 1. Configurações Globais
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
PROXY_BASE_URL = "https://drive-proxy.cmckauan.workers.dev"
FOLDER_ID = "1twEX01x0SdhtzoK58klrzP8JoDFdx6gW" # Sua pasta FILMES
LANGUAGE = "pt-BR"

if not TMDB_API_KEY:
    print("ERRO CRÍTICO: TMDB_API_KEY não foi encontrada nos Secrets!")
    exit(1)

def buscar_arquivos_do_proxy():
    """Lê a lista de arquivos diretamente do Cloudflare Worker Proxy"""
    # A maioria dos Workers expõe os dados via requisição POST/GET no endpoint de pasta
    url_pasta = f"{PROXY_BASE_URL}/folder/{FOLDER_ID}"
    headers = {"Accept": "application/json"}
    
    try:
        response = requests.get(url_pasta, headers=headers, timeout=15)
        if response.status_code == 200 and "json" in response.headers.get("Content-Type", ""):
            dados = response.json()
            # Retorna a lista de arquivos se o worker retornar JSON diretamente
            return dados.get("files", dados.get("items", []))
    except Exception as e:
        print(f"Aviso ao tentar ler API da pasta: {e}")

    # Caso o worker não retorne JSON direto via GET, fazemos a chamada POST padrão de index do Worker
    try:
        response = requests.post(f"{PROXY_BASE_URL}/", json={"id": FOLDER_ID}, timeout=15)
        if response.status_code == 200:
            dados = response.json()
            return dados.get("files", dados.get("data", {}).get("files", []))
    except Exception as e:
        print(f"Erro ao buscar arquivos no Worker: {e}")

    return []

def extrair_info_nome(nome_arquivo):
    """Extrai Título, Ano e ID do TMDB do nome do arquivo"""
    # Limpa extensão de vídeo (.mkv, .mp4, etc)
    nome_limpo = re.sub(r'\.(mkv|mp4|avi|webm|mov)$', '', nome_arquivo, flags=re.IGNORECASE)
    
    # Procura por 'id 123456' ou 'tmdb 123456'
    id_match = re.search(r'-(?: id| tmdb) (\d+)', nome_limpo, flags=re.IGNORECASE)
    tmdb_id = id_match.group(1) if id_match else None
    
    # Procura por ano entre parênteses ex: '(2003)'
    ano_match = re.search(r'\((\d{4})\)', nome_limpo)
    ano = ano_match.group(1) if ano_match else None
    
    # Limpa o título removendo ano, id e caracteres especiais iniciais
    titulo = re.sub(r'\((\d{4})\)', '', nome_limpo)
    titulo = re.sub(r'-(?: id| tmdb) \d+', '', titulo, flags=re.IGNORECASE)
    titulo = titulo.strip().lstrip('+').strip()
    
    return titulo, ano, tmdb_id

def buscar_tmdb(titulo, ano=None, tmdb_id=None):
    """Busca detalhes e posters do filme na API do TMDB"""
    if tmdb_id:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&language={LANGUAGE}"
        res = requests.get(url)
        if res.status_code == 200:
            return res.json()

    # Busca por nome + ano
    url_busca = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(titulo)}&language={LANGUAGE}"
    if ano:
        url_busca += f"&primary_release_year={ano}"
        
    res = requests.get(url_busca)
    if res.status_code == 200:
        dados = res.json()
        resultados = dados.get("results", [])
        if resultados:
            return resultados[0]
            
    return None

def processar_filmes():
    print("🔍 Conectando ao Proxy para ler a pasta de filmes...")
    arquivos = buscar_arquivos_do_proxy()
    
    if not arquivos:
        print("⚠️ Nenhum arquivo foi retornado do Proxy. Verifique se a pasta está pública ou acessível.")
        return

    print(f"📦 Encontrados {len(arquivos)} arquivos no Proxy. Processando com o TMDB...\n")

    metas = []
    
    os.makedirs("catalog/movie", exist_ok=True)
    os.makedirs("stream/movie", exist_ok=True)

    for item in arquivos:
        # Pega o nome e o ID do arquivo no Google Drive / Worker
        nome_arq = item.get("name") or item.get("title", "")
        file_id = item.get("id")
        
        # Ignora se for pasta ou não for arquivo de vídeo
        if not nome_arq or not any(nome_arq.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
            continue
            
        stream_url = f"{PROXY_BASE_URL}/file/{file_id}"
        
        titulo, ano, tmdb_id = extrair_info_nome(nome_arq)
        info_tmdb = buscar_tmdb(titulo, ano, tmdb_id)
        
        if info_tmdb:
            movie_id = f"tmdb:{info_tmdb.get('id')}"
            poster_path = info_tmdb.get("poster_path")
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
            
            # 1. Catálogo
            metas.append({
                "id": movie_id,
                "type": "movie",
                "name": info_tmdb.get("title") or titulo,
                "poster": poster_url,
                "description": info_tmdb.get("overview", "Sem sinopse disponível."),
                "releaseInfo": (info_tmdb.get("release_date") or "")[:4]
            })
            
            # 2. Rota de Stream individual
            stream_data = {
                "streams": [
                    {
                        "name": "Drive Proxy",
                        "title": f"Assistir 1080p\n{nome_arq}",
                        "url": stream_url
                    }
                ]
            }
            
            with open(f"stream/movie/{movie_id}.json", "w", encoding="utf-8") as f:
                json.dump(stream_data, f, ensure_ascii=False, indent=2)
                
            print(f"✅ Mapeado: {info_tmdb.get('title')} ({movie_id})")
        else:
            print(f"❌ Não encontrado no TMDB: '{titulo}' (Arquivo: {nome_arq})")

    # 3. Salvar o arquivo de catálogo
    catalog_data = {"metas": metas}
    
    with open("catalog/movie/meus_filmes.json", "w", encoding="utf-8") as f:
        json.dump(catalog_data, f, ensure_ascii=False, indent=2)

    with open("catalog.json", "w", encoding="utf-8") as f:
        json.dump(catalog_data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 Sucesso! {len(metas)} filmes sincronizados do seu Proxy para o Stremio!")

if __name__ == "__main__":
    processar_filmes()
