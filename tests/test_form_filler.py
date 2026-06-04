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
    assert field_type == "div"


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
    first_loc.count = AsyncMock(return_value=1)

    async def evaluate_side_effect(expr):
        if "tagName" in expr:
            return "input"
        if "checked" in expr:
            return False
        return None

    first_loc.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    first_loc.click = AsyncMock()

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
    opt1.evaluate = AsyncMock()

    opt2 = AsyncMock()
    opt2.is_visible = AsyncMock(return_value=True)
    opt2.inner_text = AsyncMock(return_value="No")
    opt2.get_attribute = AsyncMock(return_value="")
    opt2.evaluate = AsyncMock()

    options_loc.nth = MagicMock(side_effect=[opt1, opt2])
    container.locator.return_value = options_loc

    success, error, selector_used, method_used = await filler._fill_field(container, "div", "Yes")
    assert success is True, f"Error: {error}"
    assert error is None
    opt1.evaluate.assert_called_once()
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
    
    mock_input_loc = AsyncMock()
    mock_input_loc.count = AsyncMock(return_value=1)
    container.locator = MagicMock(return_value=mock_input_loc)
    
    question_locator = AsyncMock()
    question_locator.count = AsyncMock(side_effect=[1, 1, 0, 0, 0])
    question_locator.nth = MagicMock(return_value=container)
    
    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)
    
    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator
        
    page.locator = MagicMock(side_effect=locator_side_effect)
    
    # Mock text extraction and helper methods
    filler._extract_text = AsyncMock(return_value="Have you ever used Python?")
    filler._detect_field_type = AsyncMock(return_value="input")
    filler._get_field_options = AsyncMock(return_value=[])
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
    repo.save_question.assert_called_once()
    assert len(report.filled) == 1
    assert report.filled[0].answer_used == "Yes"


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
    
    mock_input_loc = AsyncMock()
    mock_input_loc.count = AsyncMock(return_value=1)
    container.locator = MagicMock(return_value=mock_input_loc)
    
    question_locator = AsyncMock()
    question_locator.count = AsyncMock(side_effect=[1, 1, 0, 0, 0])
    question_locator.nth = MagicMock(return_value=container)
    
    other_locator = AsyncMock()
    other_locator.count = AsyncMock(return_value=0)
    
    def locator_side_effect(selector):
        if any(x in selector for x in ("question", "chatbot", "botMsg", "form-field")):
            return question_locator
        return other_locator
        
    page.locator = MagicMock(side_effect=locator_side_effect)
    
    # Mock text extraction and helper methods
    filler._extract_text = AsyncMock(return_value="Years of Experience")
    filler._detect_field_type = AsyncMock(return_value="div")
    filler._get_field_options = AsyncMock(return_value=["0-1 years", "1-2 years", "2-4 years"])
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
    repo.save_answer_mapping.assert_called_once_with("years_of_experience", "1.5", "1-2 years")
    # The final report should contain the successful mapped result
    assert len(report.filled) == 1
    assert report.filled[0].status == "filled"
    assert report.filled[0].answer_used == "1-2 years"


@pytest.mark.asyncio
async def test_interactive_prompt_user_case1() -> None:
    """_interactive_prompt_user should inject dialog and return response once set on window object."""
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)
    page.wait_for_timeout = AsyncMock()
    
    res = {"answer": "1.5", "selected_option": None}
    page.evaluate = AsyncMock(side_effect=[
        None, # initial inject
        True, # exists check
        None, # response is None
        True, # exists check
        res,  # response is received
        None, # cleanup
    ])

    response = await filler._interactive_prompt_user(
        page=page,
        question_text="How many years of experience?",
        is_case2=False,
        options=[]
    )
    assert response == res
    assert page.evaluate.call_count == 6


@pytest.mark.asyncio
async def test_interactive_prompt_user_case2() -> None:
    """_interactive_prompt_user should inject dialog with options for case 2."""
    settings = load_settings()
    selectors = load_selectors()
    repo = MagicMock()
    filler = FormFiller(settings, selectors, Path("tmp"), repo=repo)

    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)
    page.wait_for_timeout = AsyncMock()
    
    res = {"answer": "2", "selected_option": "2-4 years"}
    page.evaluate = AsyncMock(side_effect=[
        None, # initial inject
        True, # exists check
        res,  # response is received
        None, # cleanup
    ])

    response = await filler._interactive_prompt_user(
        page=page,
        question_text="Select your experience level",
        is_case2=True,
        stored_answer="2",
        options=["0-1 years", "2-4 years"]
    )
    assert response == res
    assert page.evaluate.call_count == 4


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
        assert "fuzzy option <4 years" in selector_used
        opt2.evaluate.assert_called_once()
        
    finally:
        if original_profile is not None:
            profile_path.write_text(original_profile, encoding="utf-8")
        elif profile_path.exists():
            profile_path.unlink()






