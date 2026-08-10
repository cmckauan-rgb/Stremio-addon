import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

# 1. Configurações Globais
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
PROXY_BASE_URL = "https://drive-proxy.cmckauan.workers.dev"

FILMES_FOLDER_ID = "1twEX01x0SdhtzoK58klrzP8JoDFdx6gW"
SERIES_FOLDER_ID = "1OZutaKMisH1w6W8PqKFpekyfIJpq6Rws"
LANGUAGE = "pt-BR"

GENEROS_FILMES = [
    "Ação", "Aventura", "Animação", "Cinema TV", "Comédia", "Crime", 
    "Documentário", "Drama", "Família", "Fantasia", "Ficção científica", 
    "Guerra", "História", "Mistério", "Música", "Romance", "Terror", "Thriller"
]

GENEROS_SERIES = [
    "Ação", "Aventura", "Animação", "Comédia", "Crime", "Documentário",
    "Drama", "Família", "Kids", "Mistério", "Reality", "Sci-Fi & Fantasy", "Soap", "Talk"
]

if not TMDB_API_KEY:
    print("ERRO CRÍTICO: TMDB_API_KEY não foi encontrada nos Secrets!")
    exit(1)

# --- NAVEGAÇÃO NO PROXY WORKER ---

def listar_pasta_proxy(folder_id):
    """Lê os arquivos/subpastas de uma ID de pasta via Worker"""
    url_pasta = f"{PROXY_BASE_URL}/folder/{folder_id}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html"}

    try:
        res = requests.post(f"{PROXY_BASE_URL}/", json={"id": folder_id}, headers=headers, timeout=15)
        if res.status_code == 200:
            dados = res.json()
            items = dados.get("files") or dados.get("data", {}).get("files") or dados.get("items", [])
            if items:
                return items
    except Exception:
        pass

    try:
        res = requests.get(url_pasta, headers=headers, timeout=15)
        if res.status_code == 200:
            if "application/json" in res.headers.get("Content-Type", ""):
                dados = res.json()
                return dados.get("files", dados.get("items", []))
            
            soup = BeautifulSoup(res.text, "html.parser")
            items = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                texto = a.get_text().strip()
                if "/folder/" in href:
                    fid = href.split("/folder/")[-1]
                    items.append({"name": texto, "id": fid, "mimeType": "application/vnd.google-apps.folder"})
                elif "/file/" in href or any(texto.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
                    fid = href.split("/file/")[-1] if "/file/" in href else href
                    items.append({"name": texto, "id": fid, "mimeType": "video/x-matroska"})
            return items
    except Exception:
        pass

    return []

def buscar_arquivos_recursivo(folder_id):
    """Navega recursivamente por todas as subpastas"""
    todos_arquivos = []
    itens = listar_pasta_proxy(folder_id)
    
    for item in itens:
        mime = item.get("mimeType", "")
        # Se for subpasta
        if "folder" in mime or "directory" in mime or "id" in item and not any(item.get("name", "").endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
            sub_id = item.get("id")
            if sub_id and sub_id != folder_id:
                todos_arquivos.extend(buscar_arquivos_recursivo(sub_id))
        else:
            todos_arquivos.append(item)
            
    return todos_arquivos

# --- PARSERS E METADADOS ---

def extrair_info_serie(nome_arquivo):
    """Extrai Nome da Série, Temporada (S) e Episódio (E)"""
    # Procura padrões tipo S01E02 ou s01e02 ou 1x02
    match = re.search(r'(.*?)[._\s]+[sS](\d{1,2})[eE](\d{1,2})', nome_arquivo)
    if match:
        nome_serie = match.group(1).replace('.', ' ').replace('_', ' ').strip()
        temporada = int(match.group(2))
        episodio = int(match.group(3))
        return nome_serie, temporada, episodio
    
    match_alt = re.search(r'(.*?)[._\s]+(\d{1,2})x(\d{1,2})', nome_arquivo)
    if match_alt:
        nome_serie = match_alt.group(1).replace('.', ' ').replace('_', ' ').strip()
        temporada = int(match_alt.group(2))
        episodio = int(match_alt.group(3))
        return nome_serie, temporada, episodio

    return None, None, None

def buscar_tmdb_tv(nome_serie):
    """Busca Série de TV na API do TMDB"""
    url_busca = f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={quote(nome_serie)}&language={LANGUAGE}"
    res = requests.get(url_busca)
    if res.status_code == 200:
        dados = res.json()
        resultados = dados.get("results", [])
        if resultados:
            tv_id = resultados[0]['id']
            url_det = f"https://api.themoviedb.org/3/tv/{tv_id}?api_key={TMDB_API_KEY}&language={LANGUAGE}&append_to_response=external_ids"
            res_det = requests.get(url_det)
            if res_det.status_code == 200:
                return res_det.json()
            return resultados[0]
    return None

# --- PROCESSADORES ---

def processar_series():
    print("\n📺 --- PROCESSANDO SÉRIES ---")
    arquivos = buscar_arquivos_recursivo(SERIES_FOLDER_ID)
    print(f"📦 Total de arquivos de vídeo encontrados: {len(arquivos)}")

    series_map = {} # Agrupa episódios por série
    streams_map = {} # Guardo links por ID:S:E

    for item in arquivos:
        nome_arq = item.get("name") or item.get("title", "")
        file_id = item.get("id")
        
        if not nome_arq or not any(nome_arq.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
            continue

        nome_serie, temp, ep = extrair_info_serie(nome_arq)
        if not nome_serie:
            continue

        stream_url = f"{PROXY_BASE_URL}/file/{file_id}" if not str(file_id).startswith("http") else file_id

        if nome_serie not in series_map:
            info_tmdb = buscar_tmdb_tv(nome_serie)
            if info_tmdb:
                imdb_id = info_tmdb.get("external_ids", {}).get("imdb_id")
                series_id = imdb_id if imdb_id else f"tmdb:{info_tmdb.get('id')}"
                
                poster_path = info_tmdb.get("poster_path")
                backdrop_path = info_tmdb.get("backdrop_path")
                
                series_map[nome_serie] = {
                    "id": series_id,
                    "tmdb_id": info_tmdb.get("id"),
                    "type": "series",
                    "name": info_tmdb.get("name") or nome_serie,
                    "poster": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
                    "background": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
                    "description": info_tmdb.get("overview", "Sem sinopse."),
                    "releaseInfo": (info_tmdb.get("first_air_date") or "")[:4],
                    "genres": [g.get("name") for g in info_tmdb.get("genres", []) if g.get("name")],
                    "episodios": []
                }
                print(f"✅ Mapeada Série: {series_map[nome_serie]['name']} ({series_id})")

        if nome_serie in series_map:
            s_data = series_map[nome_serie]
            sid = s_data["id"]
            ep_key = f"{sid}:{temp}:{ep}"
            
            s_data["episodios"].append({
                "season": temp,
                "number": ep,
                "title": f"T{temp}:E{ep} - {nome_arq}"
            })

            streams_map[ep_key] = {
                "streams": [{
                    "name": "Drive Proxy",
                    "title": f"1080p | S{temp:02d}E{ep:02d}",
                    "url": stream_url
                }]
            }

    # Salva Meta e Stream de Séries
    os.makedirs("catalog/series/minhas_series", exist_ok=True)
    os.makedirs("stream/series", exist_ok=True)
    os.makedirs("meta/series", exist_ok=True)

    metas_series = []
    for s_info in series_map.values():
        meta_item = {
            "id": s_info["id"],
            "type": "series",
            "name": s_info["name"],
            "poster": s_info["poster"],
            "background": s_info["background"],
            "description": s_info["description"],
            "releaseInfo": s_info["releaseInfo"],
            "genres": s_info["genres"]
        }
        metas_series.append(meta_item)

        # Meta individual da série
        with open(f"meta/series/{s_info['id']}.json", "w", encoding="utf-8") as f:
            json.dump({"meta": meta_item}, f, ensure_ascii=False, indent=2)

    # Escreve Streams por Episódio (/stream/series/ID:S:E.json)
    for ep_key, stream_data in streams_map.items():
        with open(f"stream/series/{ep_key}.json", "w", encoding="utf-8") as f:
            json.dump(stream_data, f, ensure_ascii=False, indent=2)

    # Catálogo Geral de Séries
    catalog_series = {"metas": metas_series}
    with open("catalog/series/minhas_series.json", "w", encoding="utf-8") as f:
        json.dump(catalog_series, f, ensure_ascii=False, indent=2)

    # Catálogo de Séries por Gênero
    todos_generos_s = set(GENEROS_SERIES)
    for m in metas_series:
        for g in m.get("genres", []):
            if g: todos_generos_s.add(g)

    pasta_gen_s = "catalog/series/minhas_series"
    for gen in todos_generos_s:
        filtered = [m for m in metas_series if any(g.lower() == gen.lower() for g in m.get("genres", []))]
        
        with open(os.path.join(pasta_gen_s, f"genre={gen}.json"), "w", encoding="utf-8") as f:
            json.dump({"metas": filtered}, f, ensure_ascii=False, indent=2)
            
        gen_enc = quote(gen)
        if gen_enc != gen:
            with open(os.path.join(pasta_gen_s, f"genre={gen_enc}.json"), "w", encoding="utf-8") as f:
                json.dump({"metas": filtered}, f, ensure_ascii=False, indent=2)

    print("🎉 Mapeamento de Séries concluído com sucesso!")

# Mantenho o processador de Filmes original aqui...
def processar_filmes():
    print("\n🎬 --- PROCESSANDO FILMES ---")
    res = requests.get(f"{PROXY_BASE_URL}/folder/{FILMES_FOLDER_ID}")
    # (Processamento de filmes simplificado mantendo compatibilidade)
    # Pega itens usando a lógica anterior
    items = listar_pasta_proxy(FILMES_FOLDER_ID)
    metas = []
    
    os.makedirs("catalog/movie/meus_filmes", exist_ok=True)
    os.makedirs("stream/movie", exist_ok=True)
    os.makedirs("meta/movie", exist_ok=True)

    for item in items:
        nome_arq = item.get("name") or item.get("title", "")
        file_id = item.get("id")
        if not nome_arq or not any(nome_arq.lower().endswith(ext) for ext in ['.mkv', '.mp4', '.avi', '.webm']):
            continue
        
        stream_url = f"{PROXY_BASE_URL}/file/{file_id}" if not str(file_id).startswith("http") else file_id
        
        # Pega do TMDB
        url_busca = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(nome_arq[:15])}&language={LANGUAGE}"
        res_m = requests.get(url_busca)
        if res_m.status_code == 200 and res_m.json().get("results"):
            info = res_m.json()["results"][0]
            mid = f"tmdb:{info['id']}"
            m_item = {
                "id": mid,
                "type": "movie",
                "name": info.get("title"),
                "poster": f"https://image.tmdb.org/t/p/w500{info.get('poster_path')}",
                "background": f"https://image.tmdb.org/t/p/w1280{info.get('backdrop_path')}",
                "description": info.get("overview"),
                "releaseInfo": (info.get("release_date") or "")[:4],
                "genres": []
            }
            metas.append(m_item)
            
            with open(f"meta/movie/{mid}.json", "w", encoding="utf-8") as f:
                json.dump({"meta": m_item}, f, ensure_ascii=False, indent=2)

            with open(f"stream/movie/{mid}.json", "w", encoding="utf-8") as f:
                json.dump({"streams": [{"name": "Drive Proxy", "title": f"1080p | {nome_arq}", "url": stream_url}]}, f, ensure_ascii=False, indent=2)

    if metas:
        with open("catalog/movie/meus_filmes.json", "w", encoding="utf-8") as f:
            json.dump({"metas": metas}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    processar_filmes()
    processar_series()
