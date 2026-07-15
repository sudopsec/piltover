from piltover.context import NeedContextValuesContext
from piltover.tl import types
from piltover.tl.serialization_context import EMPTY_SERIALIZATION_CONTEXT, SerializationContext


class PollResultsToFormat(types.PollResultsToFormatInternal):
    min_override: bool | None = None

    def _show_solution(self, ctx: SerializationContext) -> bool:
        if self.solution is None:
            return False
        if ctx.values is None or self.id not in ctx.values.poll_answers:
            return False
        selected = ctx.values.poll_answers[self.id]
        return any(result.id in selected and not result.correct for result in self.results)

    def _min_flag(self, ctx: SerializationContext) -> bool:
        if self.min_override is not None:
            return self.min_override
        return ctx.values is None or self.id not in ctx.values.poll_answers

    def _write(self, ctx: SerializationContext) -> bytes:
        show_solution = self._show_solution(ctx)
        return types.PollResults(
            min=self._min_flag(ctx),
            results=self.results,
            total_voters=self.total_voters,
            solution=self.solution if show_solution else None,
            solution_entities=self.solution_entities if show_solution else None,
        ).write(ctx)

    def write(self, ctx: SerializationContext = EMPTY_SERIALIZATION_CONTEXT) -> bytes:
        if ctx.dont_format:
            return super().write(ctx)
        return self._write(ctx)

    def check_for_ctx_values(self, values: NeedContextValuesContext) -> None:
        values.poll_answers.add(self.id)
