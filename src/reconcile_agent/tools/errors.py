class ToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)

def map_tool_error(error: Exception) -> ToolError:
    from reconcile_agent.business.errors import AdapterError
    if isinstance(error, ToolError):
        return error
    if isinstance(error, AdapterError):
        codes = {"SCOPE_DENIED", "CASE_UNAVAILABLE", "TOOL_TIMEOUT", "SQLITE_BUSY",
                 "CONTENT_INTEGRITY_ERROR", "IMPORT_INVARIANT_ERROR", "BUSINESS_SCHEMA_MISMATCH",
                 "OUTPUT_LIMIT_EXCEEDED", "TRIAL_MISSING", "STORAGE_ERROR", "ASOF_OUT_OF_RANGE"}
        if error.code in codes:
            return ToolError(error.code, "업무 저장소 검사를 완료하지 못했습니다.",
                             retryable=error.code in {"TOOL_TIMEOUT", "SQLITE_BUSY"})
    return ToolError("INTERNAL_ERROR", "도구 실행을 완료하지 못했습니다.")
