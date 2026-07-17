"""LLM clients - the only file that knows which AI provider we use.

ChatOpenAI wraps the chat completions API, OpenAIEmbeddings wraps the
embeddings API. Both read OPENAI_API_KEY from the environment (loaded from
.env by config).
"""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app import config

# temperature=0 -> as deterministic as the model allows; extraction needs
# stable parsing, not variety.
llm = ChatOpenAI(model=config.OPENAI_MODEL, temperature=0)

# Match model strength to decision stakes: the reconcile judge decides what
# enters / leaves live memory (one joint call per source, offline) - it gets
# the stronger model. Cheap-and-many stays on `llm`; rare-and-critical here.
reconcile_judge_llm = ChatOpenAI(model=config.RECONCILE_JUDGE_MODEL, temperature=0)

embedder = OpenAIEmbeddings(model=config.EMBEDDING_MODEL)
