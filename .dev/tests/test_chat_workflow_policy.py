"""Server phase guards and stable evidence are independent of model prose."""
from copy import deepcopy
import importlib.util

import pytest

from bervel.core.errors import CoreError


def module():
    assert importlib.util.find_spec('bervel.modules.chat.workflow') is not None, 'Server workflow policy missing'
    from bervel.modules.chat import workflow
    return workflow


def test_initial_public_state_has_exact_budget_and_no_private_evidence():
    workflow = module()
    state = workflow.initial_workflow()
    public = workflow.public_workflow(state)
    assert state['version'] == 0 and public['phase'] == 'research'
    assert public['max_research_rounds'] == 4 and public['max_web_searches'] == 2
    assert public['max_research_seconds'] == 240
    assert public['history'][0]['phase'] == 'research'
    state['draft'] = {'content': 'private raw draft'}
    state['evidence'] = {'entries': ['raw source']}
    assert 'draft' not in workflow.public_workflow(state) and 'evidence' not in workflow.public_workflow(state)


@pytest.mark.parametrize('phase', ['research', 'synthesizing', 'generating', 'verifying', 'completed', 'partial', 'blocked'])
def test_phase_cannot_be_rewound_or_updated_after_terminal(phase):
    workflow = module()
    previous = workflow.initial_workflow()
    previous['phase'] = phase
    next_value = {**deepcopy(previous), 'version': 1, 'phase': 'research'}
    if phase == 'research':
        workflow.validate_transition(previous, next_value)
    else:
        with pytest.raises(CoreError):
            workflow.validate_transition(previous, next_value)


@pytest.mark.parametrize('field,value', [('research_rounds', 4), ('web_searches', 2), ('elapsed_seconds', 240)])
def test_each_research_budget_boundary_is_sufficient_to_stop_more_collection(field, value):
    workflow = module()
    state = workflow.initial_workflow()
    assert not workflow.research_exhausted(state)
    state[field] = value
    assert workflow.research_exhausted(state)


def test_large_later_sources_do_not_shrink_an_earlier_notebook_entry():
    workflow = module()
    state = workflow.initial_workflow()
    first = {'kind': 'web', 'answer': 'Zeta has 3 forging lines, output is unknown. https://zeta.example/plant',
        'sources': [{'title': 'Zeta plant', 'url': 'https://zeta.example/plant'}]}
    workflow.remember(state, first, 'web.search', 0)
    before = deepcopy(workflow.evidence_context(state))
    for number in range(1, 5):
        workflow.remember(state, {'kind': 'web', 'answer': ('Unrelated later evidence. ' * 9000),
            'sources': [{'title': 'Later', 'url': f'https://other.example/{number}'}]}, 'web.search', number)
    after = workflow.evidence_context(state)
    assert before['entries'][0] == after['entries'][0]
    assert after['coverage']['truncated'] is True
