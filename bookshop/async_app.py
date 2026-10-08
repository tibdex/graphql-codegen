from bookshop.app_graphql import GetBook
from bookshop.async_transport import AsyncClient
from bookshop.scalar import ISBN


async def title(isbn: ISBN, /, *, client: AsyncClient) -> str:
    data = await client(GetBook({"lookup": {"isbn": isbn}}), timeout=5.0)
    return data["book"]["title"]
