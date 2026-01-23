from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .database import Base

class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    jules_api_key = Column(String, nullable=True)
    github_username = Column(String, nullable=True)
    github_token = Column(String, nullable=True)

class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True) # e.g., "owner/repo" or custom container name
    status = Column(String, default="pending") # pending, active, building, error
    last_checked = Column(DateTime(timezone=True), nullable=True)
    last_error_hash = Column(String, nullable=True)
    local_path = Column(String, nullable=True) # Path where it's cloned

    # Configuration for Container
    container_name = Column(String, nullable=True) # Custom container name
    port_mappings = Column(Text, nullable=True) # JSON string: {"80/tcp": 8080}
    volume_mappings = Column(Text, nullable=True) # JSON string: {"/host/path": {"bind": "/container/path", "mode": "rw"}}
    env_vars = Column(Text, nullable=True) # JSON string: {"KEY": "VALUE"}

    error_logs = relationship("ErrorLog", back_populates="repository")

class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"))
    repository = relationship("Repository", back_populates="error_logs")
    error_hash = Column(String, index=True)
    error_message = Column(Text)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Jules Integration
    jules_session_id = Column(String, nullable=True)
    pr_url = Column(String, nullable=True)
    fix_status = Column(String, default="reported") # reported, pr_created, resolved
