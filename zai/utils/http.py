from typing import Any

import httpx

from config.settings import get_settings


async def fetch_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int | None = None,
) -> dict[str, Any] | list[Any]:
    settings = get_settings()
    t = timeout or settings.api_timeout
    try:
        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise RuntimeError(f"请求超时（>{t}s）: {url}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"API 错误 {e.response.status_code}: {url}")
    except httpx.RequestError as e:
        raise RuntimeError(f"网络请求失败: {e}")


async def fetch_json_post(
    url: str,
    body: dict,
    *,
    headers: dict | None = None,
    timeout: int | None = None,
) -> dict[str, Any] | list[Any]:
    settings = get_settings()
    t = timeout or settings.api_timeout
    hdrs = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.post(url, json=body, headers=hdrs)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise RuntimeError(f"请求超时（>{t}s）: {url}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"API 错误 {e.response.status_code}: {url}")
    except httpx.RequestError as e:
        raise RuntimeError(f"网络请求失败: {e}")
