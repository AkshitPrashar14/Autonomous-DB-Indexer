"""
DBAutonomy — Typed Exceptions

Every exception that crosses a component boundary must be one of these.
This makes error handling in AgentWorker explicit and auditable.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class DBAutonomyError(Exception):
    """Base exception for all DBAutonomy errors."""
    retryable: bool = False

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class RetryableError(DBAutonomyError):
    """Errors that should be retried."""
    retryable: bool = True


class FatalError(DBAutonomyError):
    """Errors that should not be retried. Job is permanently failed."""
    retryable: bool = False


# ---------------------------------------------------------------------------
# Log Parsing
# ---------------------------------------------------------------------------

class ParseError(FatalError):
    """Log parsing failed completely (both LLM and regex fallback failed)."""


# ---------------------------------------------------------------------------
# Schema Inspection
# ---------------------------------------------------------------------------

class SchemaNotFoundError(FatalError):
    """The queried table does not exist in the production database."""


class SchemaInspectionError(RetryableError):
    """Transient failure reading schema (e.g., connection error)."""


# ---------------------------------------------------------------------------
# Candidate Generation
# ---------------------------------------------------------------------------

class CandidateGenerationError(RetryableError):
    """Gemini failed to generate candidates after all retries."""


class NoCandidatesError(FatalError):
    """All generated candidates failed validation."""


class CandidateValidationError(FatalError):
    """A specific candidate failed Pydantic validation."""


class GeminiRateLimitError(RetryableError):
    """Gemini returned 429. Will retry with exponential backoff."""


class GeminiAPIError(RetryableError):
    """Gemini returned a non-rate-limit error."""


# ---------------------------------------------------------------------------
# Ollama / Local Parser
# ---------------------------------------------------------------------------

class OllamaTimeoutError(RetryableError):
    """Ollama did not respond within the timeout. Regex fallback used."""


class OllamaConnectionError(RetryableError):
    """Cannot connect to Ollama. May indicate service not running."""


# ---------------------------------------------------------------------------
# Shadow Database
# ---------------------------------------------------------------------------

class ShadowUnavailableError(RetryableError):
    """Shadow database is not reachable."""


class ShadowSetupError(RetryableError):
    """Failed to clone schema or populate data on shadow DB."""


class BenchmarkTimeoutError(RetryableError):
    """Benchmark query exceeded the allowed execution time."""


class BenchmarkError(RetryableError):
    """General benchmark failure."""


# ---------------------------------------------------------------------------
# Safety Gate
# ---------------------------------------------------------------------------

class SafetyGateError(FatalError):
    """Safety gate raised an unexpected internal error (not a rejection)."""


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

class DeploymentError(FatalError):
    """
    Production deployment failed.
    This is a critical error — requires operator attention.
    """


class DeploymentPreconditionError(FatalError):
    """Attempted to deploy without an approved SafetyDecision."""


# ---------------------------------------------------------------------------
# Bandit
# ---------------------------------------------------------------------------

class BanditStateCorruptError(FatalError):
    """Loaded bandit state is invalid (e.g., non-positive-definite A matrix)."""


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class JobQueueError(RetryableError):
    """Redis queue operation failed."""


class JobMaxRetriesExceeded(FatalError):
    """Job has exceeded its maximum retry attempts."""


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

class StartupError(FatalError):
    """A required dependency is unavailable at startup."""
