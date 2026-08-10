import os
import re
import json
import requests

# Configurações do seu proxy e chave
PROXY_URL = "https://drive-proxy.cmckauan.workers.dev"
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

def buscar_tmdb_filme(nome_limpo, ano=None, tmdb_id=None):
    """Busca os metadados do filme na API do TMDB"""
    if tmdb_id:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&language=pt-BR"
        res = requests.get(url).json()
        if "id" in res:
            return res

    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={nome_limpo}&language=pt-BR"
    if ano:
        url += f"&year={ano}"
    
    res = requests.get(url).json()
    if res.get("results"):
        return res["results"][0]
    return None

def processar_filmes():
    # Tenta obter a lista de filmes direto do proxy
    # O script assume a estrutura de arquivos no diretório /FILMES/
    filmes_url = f"{PROXY_URL}/FILMES/"
    print("Processando catálogo de filmes...")

    # Garante que as pastas de saída existam no repositório
    os.makedirs("catalog/movie", exist_ok=True)
    os.makedirs("stream/movie", exist_ok=True)

    catalog_metas = []

    # Fazemos uma requisição ao proxy para capturar os links/HTML dos arquivos
    try:
        html = requests.get(filmes_url).text
        # Procura por links de arquivos com extensão de vídeo (.mkv, .mp4, .avi)
        arquivos = re.findall(r'href="([^"]+\.(?:mkv|mp4|avi))"', html)
        if not arquivos:
            # Alternativa: regex para capturar links em formato de texto/JSON do worker
            arquivos = re.findall(r'([^\/\s"]+\.(?:mkv|mp4|avi))', html)
    except Exception as e:
        print(f"Erro ao acessar proxy: {e}")
        arquivos = []

    for arq in set(arquivos):
        # Decodifica URL encoding se houver (%20 -> espaço)
        nome_arquivo = requests.utils.unquote(arq)
        
        # Tenta extrair ID do TMDB direto do nome do arquivo (ex: - id 300571)
        tmdb_match = re.search(r'- id (\d+)', nome_arquivo)
        tmdb_id = tmdb_match.group(1) if tmdb_match else None

        # Tenta extrair o ano (ex: (2021) ou 2021)
        ano_match = re.search(r'\(?(\d{4})\)?', nome_arquivo)
        ano = ano_match.group(1) if ano_match else None

        # Limpa o nome removendo extensões, IDs e anos para fazer a busca
        nome_limpo = re.sub(r'\.(mkv|mp4|avi)$', '', nome_arquivo)
        nome_limpo = re.sub(r'- id \d+', '', nome_limpo)
        nome_limpo = re.sub(r'\(?\d{4}\)?', '', nome_limpo).strip()

        dados = buscar_tmdb_filme(nome_limpo, ano, tmdb_id)

        if dados:
            movie_id = f"tmdb:{dados['id']}"
            poster = f"https://image.tmdb.org/t/p/w500{dados.get('poster_path')}" if dados.get('poster_path') else ""
            
            # Adiciona ao catálogo geral
            catalog_metas.append({
                "id": movie_id,
                "type": "movie",
                "name": dados.get('title', nome_limpo),
                "poster": poster,
                "description": dados.get('overview', 'Sem sinopse disponível.')
            })

            # Monta a URL direta do vídeo apontando para o seu proxy
            stream_url = f"{PROXY_URL}/FILMES/{requests.utils.quote(nome_arquivo)}"

            # Cria o JSON individual de stream para o Stremio
            stream_payload = {
                "streams": [
                    {
                        "title": "Assistir em HD (Seu Proxy)",
                        "url": stream_url
                    }
                ]
            }

            with open(f"stream/movie/{movie_id}.json", "w", encoding="utf-8") as f:
                json.dump(stream_payload, f, indent=2, ensure_ascii=False)

    # Atualiza o arquivo catalog/movie/meus_filmes.json
    with open("catalog/movie/meus_filmes.json", "w", encoding="utf-8") as f:
        json.dump({"metas": catalog_metas}, f, indent=2, ensure_ascii=False)

    print("Gerador concluído com sucesso!")

if __name__ == "__main__":
    processar_filmes()
