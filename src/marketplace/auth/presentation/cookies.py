from fastapi import Response


def set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        "refreshToken",
        token,
        max_age=max_age,
        path="/v1/auth",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        "refreshToken",
        path="/v1/auth",
        secure=True,
        httponly=True,
        samesite="strict",
    )
