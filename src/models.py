from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.sql import func
from .database import Base

class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    jules_api_key = Column(String, nullable=True)

class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True) # e.g., "owner/repo"
    status = Column(String, default="pending") # pending, active, building, error
    last_checked = Column(DateTime(timezone=True), nullable=True)
    last_error_hash = Column(String, nullable=True)
    local_path = Column(String, nullable=True) # Path where it's cloned

class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"))
    error_hash = Column(String, index=True)
    error_message = Column(Text)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
