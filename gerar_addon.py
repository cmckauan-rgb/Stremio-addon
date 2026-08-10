import os
import json
import requests

# 1. Obter a chave da API das variáveis de ambiente (Configurada nos Secrets do GitHub)
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

if not TMDB_API_KEY:
    print("ERRO: A variável TMDB_API_KEY não foi encontrada!")
    exit(1)

# 2. Configurações da API do TMDB
BASE_URL = "https://api.themoviedb.org/3"
LANGUAGE = "pt-BR"

def buscar_filmes_populares():
    """Busca filmes populares no TMDB e formata para o Stremio"""
    url = f"{BASE_URL}/movie/popular?api_key={TMDB_API_KEY}&language={LANGUAGE}&page=1"
    
    response = requests.get(url)
    
    print(f"Status Code da API: {response.status_code}")
    
    if response.status_code != 200:
        print(f"Erro ao acessar TMDB: {response.text}")
        return []

    dados = response.json()
    resultados = dados.get("results", [])
    
    print(f"Total de filmes retornados pela API: {len(resultados)}")
    
    metas = []
    for item in resultados:
        # Pega a imagem do poster no TMDB
        poster_path = item.get("poster_path")
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        # Monta o objeto no formato aceito pelo Stremio
        meta_item = {
            "id": f"tmdb:{item.get('id')}",
            "type": "movie",
            "name": item.get("title"),
            "poster": poster_url,
            "description": item.get("overview", "Sem descrição disponível."),
            "releaseInfo": item.get("release_date", "")[:4] if item.get("release_date") else ""
        }
        metas.append(meta_item)
        
    return metas

def gerar_manifesto():
    """Gera a estrutura base do Addon para o Stremio"""
    return {
        "id": "org.meuaddon.tmdb.filmes",
        "version": "1.0.0",
        "name": "Catálogo de Filmes Populares",
        "description": "Addon com lista atualizada de filmes populares via TMDB",
        "resources": ["catalog"],
        "types": ["movie"],
        "catalogs": [
            {
                "type": "movie",
                "id": "top_movies",
                "name": "Filmes Populares"
            }
        ]
    }

def main():
    print("Iniciando a busca de filmes...")
    filmes = buscar_filmes_populares()
    
    if not filmes:
        print("AVISO: Nenhum filme foi retornado. Verifique a chave TMDB_API_KEY.")
        return

    # Monta a estrutura final do JSON
    addon_data = {
        "manifest": gerar_manifesto(),
        "metas": filmes
    }

    # Salva o arquivo catalog.json
    nome_arquivo = "catalog.json"
    with open(nome_arquivo, "w", encoding="utf-8") as f:
        json.dump(addon_data, f, ensure_ascii=False, indent=2)

    print(f"Sucesso! {len(filmes)} filmes salvos no arquivo {nome_arquivo}.")

if __name__ == "__main__":
    main()
