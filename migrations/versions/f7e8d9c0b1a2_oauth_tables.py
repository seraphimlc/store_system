# -*- coding: utf-8 -*-
"""MCP OAuth（SSO）三表：oauth_clients / oauth_codes / oauth_refresh_tokens。

规格：docs/specs-mcp-oauth.md §三。库内只存 token 摘要（sha256），明文不落库；
`oauth_codes.access_token_id` 用于 code 重放时吊销已换出的 access token（§五.2）。
"""
from alembic import op
import sqlalchemy as sa

revision = "f7e8d9c0b1a2"
down_revision = "a4b5c6d7e8f9"


def upgrade():
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(64), nullable=True),
        sa.Column("client_name", sa.String(128), nullable=False,
                  server_default=""),
        sa.Column("redirect_uris", sa.JSON(), nullable=False,
                  server_default=sa.text("('[]')")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients",
                    ["client_id"])
    op.create_table(
        "oauth_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("code_challenge_method", sa.String(16), nullable=False,
                  server_default="S256"),
        sa.Column("scope", sa.String(64), nullable=False, server_default="read"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("access_token_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oauth_codes_code_hash", "oauth_codes", ["code_hash"])
    op.create_index("ix_oauth_codes_client_id", "oauth_codes", ["client_id"])
    op.create_index("ix_oauth_codes_user_id", "oauth_codes", ["user_id"])
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False, server_default="read"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_oauth_refresh_tokens_token_hash",
                    "oauth_refresh_tokens", ["token_hash"])
    op.create_index("ix_oauth_refresh_tokens_client_id",
                    "oauth_refresh_tokens", ["client_id"])
    op.create_index("ix_oauth_refresh_tokens_user_id",
                    "oauth_refresh_tokens", ["user_id"])


def downgrade():
    op.drop_index("ix_oauth_refresh_tokens_user_id", "oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_client_id", "oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_token_hash", "oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")
    op.drop_index("ix_oauth_codes_user_id", "oauth_codes")
    op.drop_index("ix_oauth_codes_client_id", "oauth_codes")
    op.drop_index("ix_oauth_codes_code_hash", "oauth_codes")
    op.drop_table("oauth_codes")
    op.drop_index("ix_oauth_clients_client_id", "oauth_clients")
    op.drop_table("oauth_clients")
