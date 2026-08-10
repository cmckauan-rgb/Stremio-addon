import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

# 1. Configurações Globais
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
PROXY_BASE_URL = "https://drive-proxy.cmckauan.workers.dev"
FOLDER_ID = "1twEX01x0SdhtzoK58klrzP8JoDFdx6gW"
LANGUAGE = "pt-BR"

# Lista de gêneros igual à declarada no seu manifest.json
GENEROS_OFICIAIS = [
    "Ação", "Aventura", "Animação", "Cinema TV", "Comédia", "Crime", 
    "Documentário", "Drama", "Família", "Fantasia", "Ficção científica", 
    "Guerra", "História", "Mistério", "Música", "Romance", "Terror", "Thriller"
]

if not TMDB_API_KEY:
    print("ERRO CRÍTICO: TMDB_API_KEY não foi encontrada nos Secrets!")
    exit(1)

def buscar_arquivos_do_proxy():
    """Busca os arquivos da pasta do Worker tratando JSON e HTML"""
    url_pasta = f"{PROXY_BASE_URL}/folder/{FOLDER_ID}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/html"
    }

    print(f"🔗 Conectando ao Proxy: {url_pasta}")
    
    try:
        res = requests.post(f"{PROXY_BASE_URL}/", json={"id": FOLDER_ID}, headers=headers, timeout=15)
        if res.status_code == 200:
            dados = res.json()
            files = dados.get("files") or dados.get("data", {}).get("files")
            if files:
                print("✅ Arquivos recuperados via API POST!")
                return files
    except Exception as e:
        print(f"Tentativa POST falhou: {e}")

    try:
        res = requests.get(url_pasta, headers=headers, timeout=15)
        if res.status_code == 200:
            if "application/json" in res.headers.get("Content-Type", ""):
                dados = res.json()
                return dados.get("files", dados.get("items", []))
            
            soup = BeautifulSoup(res.text, "html.parser")
            arquivos = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                texto = a.get_text().strip()
                if "/file/" in href or any(texto.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
                    file_id = href.split("/file/")[-1] if "/file/" in href else href
                    arquivos.append({"name": texto, "id": file_id})
            
            if arquivos:
                print("✅ Arquivos recuperados via HTML Parser!")
                return arquivos
    except Exception as e:
        print(f"Tentativa GET falhou: {e}")

    return []

def extrair_info_nome(nome_arquivo):
    """Extrai Título, Ano e ID do TMDB do nome do arquivo"""
    nome_limpo = re.sub(r'\.(mkv|mp4|avi|webm|mov)$', '', nome_arquivo, flags=re.IGNORECASE)
    
    id_match = re.search(r'-(?: id| tmdb) (\d+)', nome_limpo, flags=re.IGNORECASE)
    tmdb_id = id_match.group(1) if id_match else None
    
    ano_match = re.search(r'\((\d{4})\)', nome_limpo)
    ano = ano_match.group(1) if ano_match else None
    
    titulo = re.sub(r'\((\d{4})\)', '', nome_limpo)
    titulo = re.sub(r'-(?: id| tmdb) \d+', '', titulo, flags=re.IGNORECASE)
    titulo = titulo.strip().lstrip('+').strip()
    
    return titulo, ano, tmdb_id

def buscar_tmdb(titulo, ano=None, tmdb_id=None):
    """Busca metadados na API do TMDB garantindo external_ids"""
    if tmdb_id:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&language={LANGUAGE}&append_to_response=external_ids"
        res = requests.get(url)
        if res.status_code == 200:
            return res.json()

    url_busca = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(titulo)}&language={LANGUAGE}"
    if ano:
        url_busca += f"&primary_release_year={ano}"
        
    res = requests.get(url_busca)
    if res.status_code == 200:
        dados = res.json()
        resultados = dados.get("results", [])
        if resultados:
            tmdb_data = resultados[0]
            url_detalhes = f"https://api.themoviedb.org/3/movie/{tmdb_data['id']}?api_key={TMDB_API_KEY}&language={LANGUAGE}&append_to_response=external_ids"
            res_det = requests.get(url_detalhes)
            if res_det.status_code == 200:
                return res_det.json()
            return tmdb_data
            
    return None

def processar_filmes():
    arquivos = buscar_arquivos_do_proxy()
    
    if not arquivos:
        print("❌ ERRO: Nenhum arquivo foi retornado do Proxy.")
        return

    print(f"📦 Total de itens encontrados no Proxy: {len(arquivos)}\n")

    metas = []
    
    # Criação das pastas de saída
    os.makedirs("catalog/movie/meus_filmes", exist_ok=True)
    os.makedirs("stream/movie", exist_ok=True)
    os.makedirs("meta/movie", exist_ok=True)

    for item in arquivos:
        nome_arq = item.get("name") or item.get("title", "")
        file_id = item.get("id")
        
        if not nome_arq or not any(nome_arq.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
            continue
            
        stream_url = f"{PROXY_BASE_URL}/file/{file_id}" if not str(file_id).startswith("http") else file_id
        
        titulo, ano, tmdb_id = extrair_info_nome(nome_arq)
        info_tmdb = buscar_tmdb(titulo, ano, tmdb_id)
        
        if info_tmdb:
            imdb_id = info_tmdb.get("imdb_id") or info_tmdb.get("external_ids", {}).get("imdb_id")
            movie_id = imdb_id if imdb_id else f"tmdb:{info_tmdb.get('id')}"

            poster_path = info_tmdb.get("poster_path")
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
            
            backdrop_path = info_tmdb.get("backdrop_path")
            background_url = f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None

            generos = [g.get("name") for g in info_tmdb.get("genres", []) if g.get("name")]
            overview = info_tmdb.get("overview", "Sem sinopse disponível.")
            release_date = (info_tmdb.get("release_date") or "")[:4]
            name_title = info_tmdb.get("title") or titulo

            meta_item = {
                "id": movie_id,
                "type": "movie",
                "name": name_title,
                "poster": poster_url,
                "background": background_url,
                "description": overview,
                "releaseInfo": release_date,
                "genres": generos
            }

            metas.append(meta_item)
            
            # Gera os metadados locais (/meta/movie/{id}.json)
            meta_response = {"meta": meta_item}
            with open(f"meta/movie/{movie_id}.json", "w", encoding="utf-8") as f:
                json.dump(meta_response, f, ensure_ascii=False, indent=2)

            # Rota de Streams (/stream/movie/{id}.json)
            stream_data = {
                "streams": [
                    {
                        "name": "Drive Proxy",
                        "title": f"1080p | {nome_arq}",
                        "url": stream_url
                    }
                ]
            }
            
            with open(f"stream/movie/{movie_id}.json", "w", encoding="utf-8") as f:
                json.dump(stream_data, f, ensure_ascii=False, indent=2)
                
            print(f"✅ Mapeado: {name_title} ({movie_id}) | Gêneros: {', '.join(generos)}")
        else:
            print(f"⚠️ Não identificado no TMDB: '{titulo}'")

    if metas:
        catalog_data = {"metas": metas}
        
        # 1. Catálogo Geral (sem filtro)
        with open("catalog/movie/meus_filmes.json", "w", encoding="utf-8") as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=2)

        with open("catalog.json", "w", encoding="utf-8") as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=2)

        # 2. Geração dos arquivos filtrados por cada gênero
        pasta_generos = "catalog/movie/meus_filmes"
        for genero in GENEROS_OFICIAIS:
            metas_genero = [
                m for m in metas 
                if any(g.lower() == genero.lower() for g in m.get("genres", []))
            ]
            dados_genero = {"metas": metas_genero}
            
            # Salva o arquivo normal (ex: genre=Animação.json)
            caminho_normal = os.path.join(pasta_generos, f"genre={genero}.json")
            with open(caminho_normal, "w", encoding="utf-8") as f:
                json.dump(dados_genero, f, ensure_ascii=False, indent=2)
                
            # Salva também a versão em URL Encode (ex: genre=Anima%C3%A7%C3%A3o.json)
            genero_encoded = quote(genero)
            if genero_encoded != genero:
                caminho_encoded = os.path.join(pasta_generos, f"genre={genero_encoded}.json")
                with open(caminho_encoded, "w", encoding="utf-8") as f:
                    json.dump(dados_genero, f, ensure_ascii=False, indent=2)

        print(f"\n🎉 Sucesso! Catálogo e arquivos de gêneros gerados com sucesso!")

if __name__ == "__main__":
    processar_filmes()
