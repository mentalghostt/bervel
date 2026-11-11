"""Public pure product analysis contract, shared by independent modules."""
from .models import (Demand, StockRow, StockData, CandidateSelection, PreparedCandidates,
                     MatchResult, AllocationResult, CoverageResult, ProductionJob,
                     ResourceCalendar, ScheduleResult)
from .matching import prepare_candidates, validate_selected_evidence, inspect_candidate, catalog_characteristics
from .quantities import (allocate_stock, weighted_piece_coverage, coverage_scale,
                         convert_quantity, convert_unit_cost, normalize_unit)
from .production import production_workdays, schedule_work
# Compatibility operations let the radar keep its saved evidence and output
# shape while consuming the same pure rules through this public entry point.
from .matching import (Requirement, FAMILIES, _text as normalize_technical_text,
    _family as product_family, _number as numeric_technical_value,
    _inline as inline_requirements, _requirements as extract_requirements,
    _catalog_item as catalog_item, _evidence as requirement_evidence,
    _prepare as prepare_legacy_candidates, _saved_evidence as saved_evidence_matches)
from .quantities import (CURRENCY_PLACES, _amount as amount_decimal, _text as decimal_text,
    _scale as quantity_scale, _pieces as piece_quantity,
    _group_allocations as group_stock_allocations,
    _weighted_piece_coverage as coverage_for_rows)

__all__ = ['Demand', 'StockRow', 'StockData', 'CandidateSelection', 'PreparedCandidates',
           'MatchResult', 'AllocationResult', 'CoverageResult', 'ProductionJob',
           'ResourceCalendar', 'ScheduleResult', 'prepare_candidates',
           'validate_selected_evidence', 'allocate_stock', 'weighted_piece_coverage',
           'coverage_scale', 'convert_quantity', 'convert_unit_cost', 'normalize_unit',
           'production_workdays', 'schedule_work', 'inspect_candidate', 'catalog_characteristics',
           'Requirement', 'FAMILIES', 'normalize_technical_text', 'product_family',
           'numeric_technical_value', 'inline_requirements', 'extract_requirements',
           'catalog_item', 'requirement_evidence', 'prepare_legacy_candidates',
           'saved_evidence_matches', 'CURRENCY_PLACES', 'amount_decimal', 'decimal_text',
           'quantity_scale', 'piece_quantity', 'group_stock_allocations', 'coverage_for_rows']
