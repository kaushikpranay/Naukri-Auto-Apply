"""
Unit tests for the FormFiller auto-fill interactions.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.question_bank.form_filler import FormFiller
from app.utils.config_loader import load_selectors, load_settings


@pytest.mark.asyncio
async def test_detect_field_type_checkbox() -> None:
    """_detect_field_type should detect checkboxes when checkbox elements exist."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    checkbox_locator = AsyncMock()
    checkbox_locator.count = AsyncMock(return_value=1)

    def side_effect(selector):
        if "[role='checkbox']" in selector or "input[type='checkbox']" in selector:
            return checkbox_locator
        mock_loc = AsyncMock()
        mock_loc.count = AsyncMock(return_value=0)
        return mock_loc

    container.locator = side_effect

    field_type = await filler._detect_field_type(container)
    assert field_type == "checkbox"


@pytest.mark.asyncio
async def test_detect_field_type_div_options() -> None:
    """_detect_field_type should detect div field type when clickable options exist but no standard fields."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    options_locator = AsyncMock()
    options_locator.count = AsyncMock(return_value=2)

    def side_effect(selector):
        if "button" in selector or "role='button'" in selector or "tabindex" in selector:
            return options_locator
        mock_loc = AsyncMock()
        mock_loc.count = AsyncMock(return_value=0)
        return mock_loc

    container.locator = side_effect

    field_type = await filler._detect_field_type(container)
    assert field_type == "button-options"


@pytest.mark.asyncio
async def test_detect_field_type_not_unknown_when_city_options_exist() -> None:
    """If visible options exist, field type should never stay unknown."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    option_locator = AsyncMock()
    option_locator.count = AsyncMock(return_value=3)

    city_1 = AsyncMock()
    city_1.inner_text = AsyncMock(return_value="Hyderabad, Telangana")
    city_1.evaluate = AsyncMock(return_value="button")

    city_2 = AsyncMock()
    city_2.inner_text = AsyncMock(return_value="Gurugram, Haryana")
    city_2.evaluate = AsyncMock(return_value="button")

    skip_option = AsyncMock()
    skip_option.inner_text = AsyncMock(return_value="Skip this question")
    skip_option.evaluate = AsyncMock(return_value="button")

    option_locator.nth = MagicMock(side_effect=[city_1, city_2, skip_option])

    def side_effect(selector):
        if any(x in selector for x in ("button", "role='button'", "role='option'")):
            return option_locator
        mock_loc = AsyncMock()
        mock_loc.count = AsyncMock(return_value=0)
        return mock_loc

    container.locator = side_effect

    field_type = await filler._detect_field_type(container)
    assert field_type == "button-options"


@pytest.mark.asyncio
async def test_get_field_options_div() -> None:
    """_get_field_options should extract option texts for div-based questions."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    options_loc = AsyncMock()
    options_loc.count = AsyncMock(return_value=2)

    opt1 = AsyncMock()
    opt1.inner_text = AsyncMock(return_value="Yes")
    opt2 = AsyncMock()
    opt2.inner_text = AsyncMock(return_value="No")

    options_loc.nth = MagicMock(side_effect=[opt1, opt2])
    container.locator.return_value = options_loc

    options = await filler._get_field_options(container, "div")
    assert options == ["Yes", "No"]


@pytest.mark.asyncio
async def test_get_field_options_checkbox() -> None:
    """_get_field_options should extract option texts for checkbox-based questions."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    options_loc = AsyncMock()
    options_loc.count = AsyncMock(return_value=1)

    opt1 = AsyncMock()
    opt1.inner_text = AsyncMock(return_value="Agree to Terms")

    options_loc.nth = MagicMock(side_effect=[opt1])
    container.locator.return_value = options_loc

    options = await filler._get_field_options(container, "checkbox")
    assert options == ["Agree to Terms"]


@pytest.mark.asyncio
async def test_fill_field_checkbox() -> None:
    """_fill_field should click checkbox if current state differs from target state."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    checkbox_loc = AsyncMock()
    first_loc = AsyncMock()
    checkbox_loc.first = first_loc
    checkbox_loc.count = AsyncMock(return_value=1)
    first_loc.count = AsyncMock(return_value=1)

    checked_state = [False]
    async def evaluate_side_effect(expr):
        if "tagName" in expr:
            return "input"
        if "checked" in expr:
            return checked_state[0]
        return None

    async def click_side_effect():
        checked_state[0] = True

    first_loc.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    first_loc.click = AsyncMock(side_effect=click_side_effect)

    container.locator.return_value = checkbox_loc

    success, error, selector_used, method_used = await filler._fill_field(container, "checkbox", "yes")
    assert success is True
    assert error is None
    first_loc.click.assert_called_once()


@pytest.mark.asyncio
async def test_fill_field_div_option() -> None:
    """_fill_field should evaluate JS click event on the matched div/button option."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    options_loc = AsyncMock()
    options_loc.count = AsyncMock(return_value=2)

    opt1 = AsyncMock()
    opt1.is_visible = AsyncMock(return_value=True)
    opt1.inner_text = AsyncMock(return_value="Yes")
    opt1.get_attribute = AsyncMock(return_value="")
    opt1.evaluate = AsyncMock(return_value="selected")

    opt2 = AsyncMock()
    opt2.is_visible = MagicMock(return_value=True)
    opt2.inner_text = AsyncMock(return_value="No")
    opt2.get_attribute = AsyncMock(return_value="")
    opt2.evaluate = AsyncMock()

    options_loc.nth = MagicMock(side_effect=[opt1, opt2])
    container.locator.return_value = options_loc

    success, error, selector_used, method_used = await filler._fill_field(container, "div", "Yes")
    assert success is True, f"Error: {error}"
    assert error is None
    assert opt1.evaluate.called
    opt2.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_fill_form_case1_unknown_question() -> None:
    """fill_form should trigger fallback dialog and save new question when answer is unknown."""
    from app.models.form_fill import FieldFillResult
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    repo.get_question_answer = MagicMock(return_value=None)
    
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)
    
    # Mock page and locator count
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()
    
    container = MagicMock()
    container.is_visible = AsyncMock(return_value=True)
    container.evaluate = AsyncMock(return_value=True)
    
    mock_input_loc = AsyncMock()
    mock_input_loc.count = AsyncMock(return_value=1)
    container.locator = MagicMock(return_value=mock_input_loc)
    
    question_locator = AsyncMock()
    question_locator.count = AsyncMock(return_value=1)
    question_locator.nth = MagicMock(return_value=container)
    
    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)
    
    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator
        
    page.locator = MagicMock(side_effect=locator_side_effect)
    
    # Mock text extraction and helper methods
    filler._extract_question_label = AsyncMock(return_value="Have you ever used Python?")
    filler._detect_field_type = AsyncMock(return_value="input")
    filler._get_field_options = AsyncMock(return_value=[])
    filler._resolve_active_chatbot_question = AsyncMock(return_value=None)
    filler._capture_screenshot = AsyncMock(return_value="dummy_path")
    
    # Mock interactive prompt returning user response
    filler._interactive_prompt_user = AsyncMock(return_value={"answer": "Yes", "selected_option": None})
    
    # Mock first handle known field (returns success)
    result = FieldFillResult(
        question_key="have_you_ever_used_python",
        question_text="Have you ever used Python?",
        field_type="input",
        required=False,
        status="filled",
        answer_used="Yes",
        answer_source="USER_LEARNED"
    )
    filler._handle_known_field = AsyncMock(return_value=result)
    
    report = await filler.fill_form(page, 1, "TestCorp", "ML Engineer", [])
    
    # Verify interactive prompt was called for Case 1
    filler._interactive_prompt_user.assert_called_once_with(
        page=page,
        question_text="Have you ever used Python?",
        is_case2=False,
        options=[]
    )
    assert repo.save_question.call_count == 2
    assert len(report.filled) == 1
    assert report.filled[0].answer_used == "Yes"


@pytest.mark.asyncio
async def test_fill_form_skips_intro_message_and_processes_next_question() -> None:
    from app.models.form_fill import FieldFillResult

    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    repo.get_question_answer = MagicMock(return_value="2")
    repo.get_answer_mapping = MagicMock(return_value=None)

    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()

    intro_container = MagicMock()
    intro_container.is_visible = AsyncMock(return_value=True)
    intro_container.evaluate = AsyncMock(return_value=True)
    valid_container = MagicMock()
    valid_container.is_visible = AsyncMock(return_value=True)
    valid_container.evaluate = AsyncMock(return_value=True)

    question_locator = AsyncMock()
    question_locator.count = AsyncMock(return_value=2)
    question_locator.nth = MagicMock(side_effect=[intro_container, valid_container, intro_container, valid_container])

    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)

    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator

    page.locator = MagicMock(side_effect=locator_side_effect)

    filler._capture_screenshot = AsyncMock(return_value="dummy_path")
    filler._interactive_prompt_user = AsyncMock()
    async def extract_question_label_side_effect(container_arg, *_args, **_kwargs):
        if container_arg is intro_container:
            return "Hi Kaushik Pranay, thank you for showing interest. Kindly answer all the recruiter's questions to successfully apply for the job."
        return "How many years of experience do you have in GenAI?"

    filler._extract_question_label = AsyncMock(side_effect=extract_question_label_side_effect)
    async def detect_field_type_side_effect(container_arg, *_args, **_kwargs):
        if container_arg is intro_container:
            return "unknown"
        return "input"

    async def get_field_options_side_effect(container_arg, *_args, **_kwargs):
        if container_arg is intro_container:
            return []
        return []

    filler._detect_field_type = AsyncMock(side_effect=detect_field_type_side_effect)
    filler._get_field_options = AsyncMock(side_effect=get_field_options_side_effect)
    filler._resolve_active_chatbot_question = AsyncMock(return_value=None)
    filler._handle_known_field = AsyncMock(return_value=FieldFillResult(
        question_key="genai_experience",
        question_text="How many years of experience do you have in GenAI?",
        field_type="input",
        required=False,
        status="error",
        answer_used=None,
        error="test sentinel",
        answer_source="AUTO",
    ))

    report = await filler.fill_form(page, 1, "TestCorp", "GenAI Engineer", [])

    filler._interactive_prompt_user.assert_not_called()
    assert len(report.filled) == 1
    assert report.filled[0].question_text == "How many years of experience do you have in GenAI?"


@pytest.mark.asyncio
async def test_fill_form_handles_none_popup_response_without_crash() -> None:
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    repo.get_question_answer = MagicMock(return_value=None)

    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()

    container = MagicMock()
    container.is_visible = AsyncMock(return_value=True)
    container.evaluate = AsyncMock(return_value=True)

    question_locator = AsyncMock()
    question_locator.count = AsyncMock(return_value=1)
    question_locator.nth = MagicMock(return_value=container)

    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)

    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator

    page.locator = MagicMock(side_effect=locator_side_effect)

    filler._capture_screenshot = AsyncMock(return_value="dummy_path")
    filler._extract_question_label = AsyncMock(return_value="How many years of experience do you have in GenAI?")
    filler._detect_field_type = AsyncMock(return_value="input")
    filler._get_field_options = AsyncMock(return_value=[])
    filler._resolve_active_chatbot_question = AsyncMock(return_value=None)
    filler._interactive_prompt_user = AsyncMock(return_value=None)
    filler._handle_known_field = AsyncMock()

    from app.models.discovery import PipelineSuspendedException
    with pytest.raises(PipelineSuspendedException):
        await filler.fill_form(page, 1, "TestCorp", "GenAI Engineer", [])

    filler._handle_known_field.assert_not_called()


@pytest.mark.asyncio
async def test_fill_form_case2_unmatched_option() -> None:
    """fill_form should trigger mapping dialog when known answer has no matching option, then save the mapping and re-fill."""
    from app.models.discovery import DiscoveredQuestion
    from app.models.form_fill import FieldFillResult
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    repo.get_question_answer = MagicMock(return_value="1.5")
    repo.get_answer_mapping = MagicMock(return_value=None)
    
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)
    
    # Mock page and locator count
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock()
    
    container = MagicMock()
    container.is_visible = AsyncMock(return_value=True)
    container.evaluate = AsyncMock(return_value=True)
    
    mock_input_loc = AsyncMock()
    mock_input_loc.count = AsyncMock(return_value=1)
    container.locator = MagicMock(return_value=mock_input_loc)
    
    question_locator = AsyncMock()
    question_locator.count = AsyncMock(return_value=1)
    question_locator.nth = MagicMock(return_value=container)
    
    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)
    
    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator
        
    page.locator = MagicMock(side_effect=locator_side_effect)
    
    # Mock text extraction and helper methods
    filler._extract_question_label = AsyncMock(return_value="Years of Experience")
    filler._detect_field_type = AsyncMock(return_value="div")
    filler._get_field_options = AsyncMock(return_value=["0-1 years", "1-2 years", "2-4 years"])
    filler._resolve_active_chatbot_question = AsyncMock(return_value=None)
    filler._capture_screenshot = AsyncMock(return_value="dummy_path")
    
    # Mock interactive prompt returning user mapped option selection
    filler._interactive_prompt_user = AsyncMock(return_value={"answer": "1.5", "selected_option": "1-2 years"})
    
    # Mock first handle known field (returns error)
    err_result = FieldFillResult(
        question_key="years_of_experience",
        question_text="Years of Experience",
        field_type="div",
        required=False,
        status="error",
        answer_used=None,
        error="No option matching '1.5' found",
        answer_source="AUTO"
    )
    # Mock second handle known field (returns success)
    success_result = FieldFillResult(
        question_key="years_of_experience",
        question_text="Years of Experience",
        field_type="div",
        required=False,
        status="filled",
        answer_used="1-2 years",
        answer_source="USER_MAPPED"
    )
    filler._handle_known_field = AsyncMock(side_effect=[err_result, success_result])
    
    discovered_qs = [
        DiscoveredQuestion(
            question_key="years_of_experience",
            question_text="Years of Experience",
            field_type="div",
            required=False,
            answer="1.5"
        )
    ]
    report = await filler.fill_form(page, 1, "TestCorp", "ML Engineer", discovered_qs)
    
    # Verify mapping prompt was called
    filler._interactive_prompt_user.assert_called_once_with(
        page=page,
        question_text="Years of Experience",
        is_case2=True,
        stored_answer="1.5",
        options=["0-1 years", "1-2 years", "2-4 years"]
    )
    repo.save_answer_mapping.assert_called_once_with(
        "years_of_experience__options__0-1 years|1-2 years|2-4 years",
        "1.5",
        "1-2 years",
    )
    # The final report should contain the successful mapped result
    assert len(report.filled) == 1
    assert report.filled[0].status == "filled"
    assert report.filled[0].answer_used == "1-2 years"


@pytest.mark.asyncio
async def test_interactive_prompt_user_case1() -> None:
    """_interactive_prompt_user should delegate to _show_tkinter_popup and return response."""
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    res = {"answer": "Some answer", "selected_option": None}
    filler._show_tkinter_popup = MagicMock(return_value=res)

    response = await filler._interactive_prompt_user(
        page=page,
        question_text="How many years of experience?",
        is_case2=False,
        options=[]
    )
    assert response == res
    filler._show_tkinter_popup.assert_called_once_with(
        "How many years of experience?",
        [],
        False,
        None
    )


@pytest.mark.asyncio
async def test_interactive_prompt_user_case2() -> None:
    """_interactive_prompt_user should delegate to _show_tkinter_popup with options for case 2."""
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    res = {"answer": "2", "selected_option": "2-4 years"}
    filler._show_tkinter_popup = MagicMock(return_value=res)

    response = await filler._interactive_prompt_user(
        page=page,
        question_text="Select your experience level",
        is_case2=True,
        stored_answer="2",
        options=["0-1 years", "2-4 years"]
    )
    assert response == res
    filler._show_tkinter_popup.assert_called_once_with(
        "Select your experience level",
        ["0-1 years", "2-4 years"],
        True,
        "2"
    )


from app.question_bank.form_filler import is_valid_recruiter_question_container

@pytest.mark.asyncio
async def test_is_valid_recruiter_question_container_valid_input() -> None:
    container = MagicMock()
    
    # 1. Test standard input (has select count > 0)
    mock_select = AsyncMock()
    mock_select.count = AsyncMock(return_value=1)
    
    mock_other = AsyncMock()
    mock_other.count = AsyncMock(return_value=0)
    
    def side_effect(selector):
        if "select" in selector:
            return mock_select
        return mock_other
        
    container.locator = MagicMock(side_effect=side_effect)
    assert await is_valid_recruiter_question_container(container) is True


@pytest.mark.asyncio
async def test_is_valid_recruiter_question_container_ignored_nav_buttons() -> None:
    container = MagicMock()
    
    # 2. Test ignored buttons (only Save / Skip buttons present)
    mock_buttons = AsyncMock()
    mock_buttons.count = AsyncMock(return_value=2)
    
    mock_btn_1 = AsyncMock()
    mock_btn_1.inner_text = AsyncMock(return_value="Save")
    mock_btn_1.evaluate = AsyncMock(return_value="button")
    
    mock_btn_2 = AsyncMock()
    mock_btn_2.inner_text = AsyncMock(return_value="Skip")
    mock_btn_2.evaluate = AsyncMock(return_value="button")
    
    mock_buttons.nth = MagicMock(side_effect=[mock_btn_1, mock_btn_2])
    
    mock_other = AsyncMock()
    mock_other.count = AsyncMock(return_value=0)
    
    def side_effect(selector):
        if any(x in selector for x in ("button", "role='button'", "class*='option'")) and "radio" not in selector and "checkbox" not in selector:
            return mock_buttons
        return mock_other
        
    container.locator = MagicMock(side_effect=side_effect)
    assert await is_valid_recruiter_question_container(container) is False


@pytest.mark.asyncio
async def test_is_valid_recruiter_question_container_valid_options() -> None:
    container = MagicMock()
    
    # 3. Test valid option buttons (e.g. Yes / No buttons)
    mock_buttons = AsyncMock()
    mock_buttons.count = AsyncMock(return_value=2)
    
    mock_btn_1 = AsyncMock()
    mock_btn_1.inner_text = AsyncMock(return_value="Yes")
    mock_btn_1.evaluate = AsyncMock(return_value="button")
    
    mock_btn_2 = AsyncMock()
    mock_btn_2.inner_text = AsyncMock(return_value="No")
    mock_btn_2.evaluate = AsyncMock(return_value="button")
    
    mock_buttons.nth = MagicMock(side_effect=[mock_btn_1, mock_btn_2])
    
    mock_other = AsyncMock()
    mock_other.count = AsyncMock(return_value=0)
    
    def side_effect(selector):
        if any(x in selector for x in ("button", "role='button'", "class*='option'")) and "radio" not in selector and "checkbox" not in selector:
            return mock_buttons
        return mock_other
        
    container.locator = MagicMock(side_effect=side_effect)
    assert await is_valid_recruiter_question_container(container) is True


@pytest.mark.asyncio
async def test_repository_safeguards() -> None:
    # 4. Test that repository safeguards prevent saving navigation keywords as answers
    from app.discovery.repository import ApplyDiscoveryRepository
    from app.models.discovery import DiscoveredQuestion
    
    repo = ApplyDiscoveryRepository(Path(":memory:"))
    
    q_save = DiscoveredQuestion(
        question_key="test_key",
        question_text="Save this?",
        field_type="input",
        required=True,
        answer="Save"
    )
    repo.save_question(1, q_save)
    
    ans = repo.get_question_answer("test_key")
    # Verify that "Save" was cleared to None/null
    assert ans is None
    
    repo.save_answer_mapping("test_key", "1.5", "Continue")
    mapping = repo.get_answer_mapping("test_key", "1.5")
    # Verify that "Continue" mapping was ignored
    assert mapping is None
    
    repo.close()


@pytest.mark.asyncio
async def test_get_actual_numeric_experience() -> None:
    from app.question_bank.form_filler import get_actual_numeric_experience
    import json
    
    profile_path = Path("config/candidate_profile.json")
    original_profile = None
    if profile_path.exists():
        original_profile = profile_path.read_text(encoding="utf-8")
        
    try:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps({"experience_years": 4.5, "python_experience": 3.0}), encoding="utf-8")
        
        # Test specific key
        assert get_actual_numeric_experience("python_experience") == 3.0
        # Test general fallback
        assert get_actual_numeric_experience("no_experience") == 4.5
        # Test unknown key fallback
        assert get_actual_numeric_experience("some_weird_key") == 4.5
    finally:
        if original_profile is not None:
            profile_path.write_text(original_profile, encoding="utf-8")
        elif profile_path.exists():
            profile_path.unlink()


@pytest.mark.asyncio
async def test_fill_field_radio_fuzzy_string_matching() -> None:
    from app.question_bank.form_filler import FormFiller
    from unittest.mock import MagicMock, AsyncMock
    from pathlib import Path
    
    settings = MagicMock()
    selectors = MagicMock()
    
    import json
    profile_path = Path("config/candidate_profile.json")
    original_profile = None
    if profile_path.exists():
        original_profile = profile_path.read_text(encoding="utf-8")
        
    try:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps({"experience_years": 2.0}), encoding="utf-8")
        
        filler = FormFiller(settings, selectors, Path("tmp"))
        
        container = MagicMock()
        page = MagicMock()
        page.locator = MagicMock()
        
        # Stub page.locator for drawer search to return no visible drawers
        mock_drawer_search = AsyncMock()
        mock_drawer_search.count = AsyncMock(return_value=0)
        page.locator = MagicMock(return_value=mock_drawer_search)
        
        mock_options = AsyncMock()
        mock_options.count = AsyncMock(return_value=3)
        
        opt1 = AsyncMock()
        opt1.inner_text = AsyncMock(return_value="No experience")
        opt1.evaluate = AsyncMock(return_value="input")
        opt1.get_attribute = AsyncMock(return_value="")
        
        opt2 = AsyncMock()
        opt2.inner_text = AsyncMock(return_value="<4 years")
        opt2.evaluate = AsyncMock(return_value="input")
        opt2.get_attribute = AsyncMock(return_value="")
        
        opt3 = AsyncMock()
        opt3.inner_text = AsyncMock(return_value="4-6 years")
        opt3.evaluate = AsyncMock(return_value="input")
        opt3.get_attribute = AsyncMock(return_value="")
        
        mock_options.nth = MagicMock(side_effect=[opt1, opt2, opt3])
        
        container.locator = MagicMock(return_value=mock_options)
        
        success, error, selector_used, method_used = await filler._fill_field(
            container=container,
            field_type="radio",
            answer="<6 years",
            page=page,
            question_key="no_experience"
        )
        
        assert success is True
        assert error is None
        assert "numeric: <4 years" in selector_used
        assert opt2.evaluate.called
        
    finally:
        if original_profile is not None:
            profile_path.write_text(original_profile, encoding="utf-8")
        elif profile_path.exists():
            profile_path.unlink()


@pytest.mark.asyncio
async def test_fill_field_radio_matches_exact_visible_label_text() -> None:
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    page = MagicMock()

    mock_drawer_search = AsyncMock()
    mock_drawer_search.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=mock_drawer_search)

    radio_options = AsyncMock()
    radio_options.count = AsyncMock(return_value=3)

    yes_option = AsyncMock()
    yes_option.inner_text = AsyncMock(return_value="Yes")
    yes_option.get_attribute = AsyncMock(return_value="")
    yes_option.evaluate = AsyncMock(return_value="selected")

    no_option = AsyncMock()
    no_option.inner_text = AsyncMock(return_value="No")
    no_option.get_attribute = AsyncMock(return_value="")
    no_option.evaluate = AsyncMock()

    skip_option = AsyncMock()
    skip_option.inner_text = AsyncMock(return_value="Skip this question")
    skip_option.get_attribute = AsyncMock(return_value="")
    skip_option.evaluate = AsyncMock()

    radio_options.nth = MagicMock(side_effect=[yes_option, no_option, skip_option])
    container.locator = MagicMock(return_value=radio_options)

    success, error, selector_used, method_used = await filler._fill_field(
        container=container,
        field_type="radio",
        answer="Yes",
        page=page,
        question_key="willing_to_relocate",
    )

    assert success is True
    assert error is None
    assert selector_used == "[role='radio'] exact: Yes"
    assert method_used == "DOM_CLICK"
    assert yes_option.evaluate.called
    no_option.evaluate.assert_not_called()
    skip_option.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_detect_field_type_chatbot_chips() -> None:
    """_detect_field_type should detect chatbot_chips when visible chip elements exist."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    chip_locator = AsyncMock()
    chip_locator.count = AsyncMock(return_value=2)
    
    chip1 = AsyncMock()
    chip1.is_visible = AsyncMock(return_value=True)
    chip2 = AsyncMock()
    chip2.is_visible = AsyncMock(return_value=False)
    
    chips = [chip1, chip2]
    chip_locator.nth = MagicMock(side_effect=lambda idx: chips[idx] if idx < len(chips) else MagicMock())

    def side_effect(selector):
        if "chatbot_Chip" in selector or "chipItem" in selector:
            return chip_locator
        mock_loc = AsyncMock()
        mock_loc.count = AsyncMock(return_value=0)
        return mock_loc

    container.locator = side_effect

    field_type = await filler._detect_field_type(container)
    assert field_type == "chatbot_chips"


@pytest.mark.asyncio
async def test_get_field_options_chatbot_chips() -> None:
    """_get_field_options should extract option texts from visible chatbot chips."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    container_loc = AsyncMock()
    container_loc.count = AsyncMock(return_value=0)
    container.locator = MagicMock(return_value=container_loc)
    page = MagicMock()
    
    drawer = MagicMock()
    filler._resolve_drawer = AsyncMock(return_value=drawer)
    
    chip_locator = AsyncMock()
    chip_locator.count = AsyncMock(return_value=2)
    
    chip1 = AsyncMock()
    chip1.is_visible = AsyncMock(return_value=True)
    chip1.inner_text = AsyncMock(return_value="Yes")
    
    chip2 = MagicMock()
    chip2.is_visible = AsyncMock(return_value=True)
    chip2.inner_text = AsyncMock(return_value="No")
    
    chip_locator.nth = MagicMock(side_effect=[chip1, chip2])
    drawer.locator = MagicMock(return_value=chip_locator)

    options = await filler._get_field_options(container, "chatbot_chips", page)
    assert "Yes" in options
    assert "No" in options


@pytest.mark.asyncio
async def test_fill_field_chatbot_chips() -> None:
    """_fill_field should click the matched chatbot chip using evaluate."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    container = MagicMock()
    page = MagicMock()
    
    drawer = MagicMock()
    drawer.is_visible = AsyncMock(return_value=True)
    
    drawer_search_locator = AsyncMock()
    drawer_search_locator.count = AsyncMock(return_value=1)
    drawer_search_locator.nth = MagicMock(return_value=drawer)
    page.locator = MagicMock(return_value=drawer_search_locator)

    chip_locator = AsyncMock()
    chip_locator.count = AsyncMock(return_value=2)
    
    chip1 = AsyncMock()
    chip1.is_visible = AsyncMock(return_value=True)
    chip1.inner_text = AsyncMock(return_value="Yes")
    chip1.evaluate = AsyncMock()
    
    chip2 = AsyncMock()
    chip2.is_visible = AsyncMock(return_value=True)
    chip2.inner_text = AsyncMock(return_value="No")
    chip2.evaluate = AsyncMock(return_value="selected")
    
    chip_locator.nth = MagicMock(side_effect=[chip1, chip2])
    drawer.locator = MagicMock(return_value=chip_locator)

    success, error, selector_used, method_used = await filler._fill_field(
        container=container,
        field_type="chatbot_chips",
        answer="No",
        page=page
    )

    assert success is True
    assert error is None
    assert "chatbot_chip: no" in selector_used
    chip2.evaluate.assert_any_call("el => el.click()")


@pytest.mark.asyncio
async def test_chatbot_field_type_detection_outside_container() -> None:
    """_detect_field_type should detect chatbot_chips using fallback scope when controls are outside the container."""
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    # Container represents chatbot message - evaluate returns class includes botItem
    container = MagicMock()
    container.evaluate = AsyncMock(return_value=True)

    page = MagicMock()
    drawer = MagicMock()
    filler._resolve_drawer = AsyncMock(return_value=drawer)

    # Empty locators inside container
    empty_loc = AsyncMock()
    empty_loc.count = AsyncMock(return_value=0)
    empty_loc.nth = MagicMock(return_value=None)
    container.locator = MagicMock(return_value=empty_loc)

    # Chips inside drawer
    chip_locator = AsyncMock()
    chip_locator.count = AsyncMock(return_value=2)
    chip1 = AsyncMock()
    chip1.is_visible = AsyncMock(return_value=True)
    chip1.inner_text = AsyncMock(return_value="Yes")
    chip2 = AsyncMock()
    chip2.is_visible = AsyncMock(return_value=True)
    chip2.inner_text = AsyncMock(return_value="No")
    chip_locator.nth = MagicMock(side_effect=[chip1, chip2])

    # Let drawer.locator return chip_locator for chip selectors
    def drawer_locator_mock(selector):
        if "chatbot_Chip" in selector or "chipItem" in selector:
            return chip_locator
        return empty_loc

    drawer.locator = MagicMock(side_effect=drawer_locator_mock)

    # Call _detect_field_type
    field_type = await filler._detect_field_type(container, page)
    assert field_type == "chatbot_chips"


@pytest.mark.asyncio
async def test_job_239_regression() -> None:
    """Regression test for Job 239 chatbot chips question extraction and click validation."""
    from playwright.async_api import async_playwright
    from app.question_bank.form_filler import is_valid_recruiter_question_container
    import re

    html_path = Path(__file__).parent.parent / "artifacts" / "job_239_after.html"
    if not html_path.exists():
        pytest.skip("artifacts/job_239_after.html not found, skipping regression test.")

    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # Load the local HTML file content, stripping scripts to prevent loading issues
            html_content = html_path.read_text(encoding="utf-8")
            html_content = re.sub(
                r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>",
                "",
                html_content,
                flags=re.IGNORECASE
            )
            await page.set_content(html_content)

            # Find the bot message container
            container_sel = selectors.discovery.questions.container
            containers = page.locator(container_sel)
            count = await containers.count()
            
            target_container = None
            for i in range(count):
                container = containers.nth(i)
                if await container.is_visible() and await is_valid_recruiter_question_container(container):
                    text = await filler._extract_question_label(
                        container, selectors.discovery.questions.text, page
                    )
                    if "career break" in text.lower():
                        target_container = container
                        break

            assert target_container is not None, "Could not find the career break question container"

            # 1. Question text detected correctly
            question_text = await filler._extract_question_label(
                target_container, selectors.discovery.questions.text, page
            )
            assert "are you on a career break" in question_text.lower()

            # 2. Field type detected as chatbot_chips
            field_type = await filler._detect_field_type(target_container, page)
            assert field_type == "chatbot_chips"

            # 3. Options extracted are Yes, No
            options = await filler._get_field_options(target_container, field_type, page)
            assert "Yes" in options or any("yes" in o.lower() for o in options)
            assert "No" in options or any("no" in o.lower() for o in options)

            # 4. Clicks matching chip successfully
            success, error, selector_used, method_used = await filler._fill_field(
                container=target_container,
                field_type=field_type,
                answer="No",
                page=page
            )
            assert success is True
            assert error is None
            assert "chatbot_chip" in selector_used
            
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_job_1627_active_chatbot_question_resolution() -> None:
    from playwright.async_api import async_playwright
    import re

    html_path = Path(__file__).parent.parent / "artifacts" / "job_1627_after.html"
    if not html_path.exists():
        pytest.skip("artifacts/job_1627_after.html not found, skipping regression test.")

    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            html_content = html_path.read_text(encoding="utf-8")
            html_content = re.sub(
                r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>",
                "",
                html_content,
                flags=re.IGNORECASE,
            )
            await page.set_content(html_content)

            await page.evaluate(
                """() => {
                    const chatList = document.querySelector('.chatbot_MessageContainer .list');
                    if (!chatList) return;

                    const userItem = document.createElement('li');
                    userItem.className = 'userItem chatbot_ListItem';
                    userItem.innerHTML = `
                        <div class="chipMsg">
                            <div class="userMsg chatbot_msgWrapper"><span>Yes</span></div>
                        </div>
                    `;
                    chatList.appendChild(userItem);

                    const nextQuestion = document.createElement('li');
                    nextQuestion.className = 'botItem chatbot_ListItem';
                    nextQuestion.innerHTML = `
                        <div class="botMsg msg">
                            <div>
                                <span>Have you worked on production-level AI/ML applications?</span>
                            </div>
                        </div>
                    `;
                    chatList.appendChild(nextQuestion);

                    const newChips = document.createElement('li');
                    newChips.className = 'botChips chatbot_ListItem';
                    newChips.innerHTML = `
                        <div class="chipMsg">
                            <div class="chipMessage">
                                <div class="chatbot_Chip">Yes</div>
                                <div class="chatbot_Chip">No</div>
                            </div>
                        </div>
                    `;
                    chatList.appendChild(newChips);
                }"""
            )

            context = await filler._resolve_active_chatbot_question(page, set())

            assert context is not None
            assert context["question_text"] == "Have you worked on production-level AI/ML applications?"
            assert context["question_text"] != "Are you currently residing in Kolkata, West Bengal or willing to relocate to Kolkata, West Bengal?"
            assert context["options"] == ["Yes", "No"]
            assert context["question_key"] != "willing_to_relocate"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_interactive_prompt_user_real_submit_flow() -> None:
    """Test the real _interactive_prompt_user dialog flow with subprocess simulation."""
    from unittest.mock import patch
    
    settings = load_settings()
    selectors = load_selectors()
    filler = FormFiller(settings, selectors, Path("tmp"))

    page = MagicMock()
    
    mock_completed_proc = MagicMock()
    mock_completed_proc.returncode = 0
    mock_completed_proc.stdout = '{"answer": "Yes", "selected_option": "Yes"}'
    mock_completed_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_completed_proc) as mock_run:
        response = await filler._interactive_prompt_user(
            page=page,
            question_text="Are you willing to relocate?",
            is_case2=False,
            options=[]
        )
        
        assert response == {"answer": "Yes", "selected_option": "Yes"}
        mock_run.assert_called_once()
