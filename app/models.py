"""SQLAlchemy ORM models."""

from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer,
    String, Text, BigInteger, JSON,
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    admin = "admin"
    approver = "approver"
    editor = "editor"
    viewer = "viewer"


class CRStatus(str, enum.Enum):
    draft = "draft"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    merged = "merged"
    closed = "closed"


class FileAction(str, enum.Enum):
    create = "create"
    edit = "edit"
    delete = "delete"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(150), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    role = Column(String(20), default=UserRole.viewer.value, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    file_versions = relationship("FileVersion", back_populates="author", foreign_keys="FileVersion.author_id")
    change_requests_authored = relationship("ChangeRequest", back_populates="author", foreign_keys="ChangeRequest.author_id")
    change_requests_reviewed = relationship("ChangeRequest", back_populates="reviewer", foreign_keys="ChangeRequest.reviewer_id")
    audit_logs = relationship("AuditLog", back_populates="user")


class FileVersion(Base):
    __tablename__ = "file_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String(1024), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    s3_key = Column(String(1024), nullable=False)
    size = Column(BigInteger, default=0)
    content_hash = Column(String(64), nullable=False)  # SHA-256
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, default="")
    is_delete = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    author = relationship("User", back_populates="file_versions", foreign_keys=[author_id])

    __table_args__ = (
        Index("ix_file_versions_path_version", "file_path", "version", unique=True),
    )


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    status = Column(String(30), default=CRStatus.draft.value, nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_comment = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    merged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    author = relationship("User", back_populates="change_requests_authored", foreign_keys=[author_id])
    reviewer = relationship("User", back_populates="change_requests_reviewed", foreign_keys=[reviewer_id])
    files = relationship("ChangeRequestFile", back_populates="change_request", cascade="all, delete-orphan")


class ChangeRequestFile(Base):
    __tablename__ = "change_request_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    change_request_id = Column(Integer, ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(String(1024), nullable=False)
    action = Column(String(20), nullable=False)  # create, edit, delete
    staging_s3_key = Column(String(1024), nullable=True)  # staged content key
    base_version_id = Column(Integer, ForeignKey("file_versions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    change_request = relationship("ChangeRequest", back_populates="files")
    base_version = relationship("FileVersion", foreign_keys=[base_version_id])

    __table_args__ = (
        Index("ix_cr_files_cr_path", "change_request_id", "file_path", unique=True),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(255), default="")
    details = Column(JSON, default=dict)
    ip_address = Column(String(45), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="audit_logs")


class FileShare(Base):
    __tablename__ = "file_shares"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String(1024), unique=True, nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    is_public = Column(Boolean, default=False, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    created_by = relationship("User")
