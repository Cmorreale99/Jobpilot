"""Master CV rendering (V2 M11).

``render_master_cv.py`` is the verified drop-in renderer from the V2 template pack —
integrated **as-is** (scope guard: never rewritten here; ruff excludes it). The LLM
never touches formatting; this package never touches content. The context builder
that feeds it lives in ``app/domain/resume_context.py`` and the orchestration in
``app/services/master_cv_render.py``.
"""
