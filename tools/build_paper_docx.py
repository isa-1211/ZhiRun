from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, Cm, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "论文初稿_Journal_of_Agricultural_Engineering.docx"
FIG_DIR = ROOT / "paper_figures"
FIG_DIR.mkdir(exist_ok=True)


def font_path():
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/Arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
    ]
    return next((p for p in candidates if p.exists()), None)


def make_diagram(path, workflow=False):
    width, height = (1600, 920) if not workflow else (1600, 980)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    fp = font_path()
    title_font = ImageFont.truetype(str(fp), 42) if fp else None
    box_font = ImageFont.truetype(str(fp), 28) if fp else None
    small_font = ImageFont.truetype(str(fp), 23) if fp else None
    draw.text((60, 30), "ZhiRun fertigation control loop" if not workflow else "ZhiRun safety-gated execution logic", fill="#14213d", font=title_font)

    def box(x, y, w, h, title, lines, fill="#e9f3ff", outline="#235789"):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=fill, outline=outline, width=4)
        draw.text((x + 20, y + 18), title, fill="#14213d", font=box_font)
        yy = y + 66
        for line in lines:
            draw.text((x + 20, yy), line, fill="#263238", font=small_font)
            yy += 34

    def arrow(x1, y1, x2, y2, color="#4a5568", width=6):
        draw.line((x1, y1, x2, y2), fill=color, width=width)
        import math
        angle = math.atan2(y2-y1, x2-x1)
        size = 18
        a1 = angle + 2.65
        a2 = angle - 2.65
        draw.polygon([(x2, y2), (x2 + size*math.cos(a1), y2 + size*math.sin(a1)), (x2 + size*math.cos(a2), y2 + size*math.sin(a2))], fill=color)

    if not workflow:
        box(70, 220, 300, 230, "Field sensors", ["RS485: soil and weather", "single soil-moisture probe", "rain gauge and flow meters"], "#eef7ee", "#4b7f52")
        box(470, 220, 300, 230, "RK3506B edge node", ["Modbus acquisition", "LVGL local display", "Wi-Fi/Ethernet uplink"], "#fff6df", "#a8791c")
        box(870, 160, 320, 300, "Public server", ["data validation", "ExtraTrees policy", "weather fallback", "work-order API"], "#f1edff", "#6c4ab6")
        box(1290, 220, 250, 230, "ESP32-S3", ["relay state machine", "pulse counting", "fail-safe stop"], "#ffecec", "#a43d3d")
        arrow(370, 335, 470, 335)
        arrow(770, 335, 870, 335)
        arrow(1190, 335, 1290, 335)
        draw.text((520, 505), "USB/CH341 serial command and status channel", fill="#4a5568", font=small_font)
        box(360, 620, 880, 190, "Closed-loop actuation", ["N/P/K pump + flow-meter pair independently stops at target volume", "Outlet mixing-tank pump starts only after all fertilizer channels finish", "Browser dashboard and local HMI expose the same state fields"], "#f5f5f5", "#657786")
        arrow(1025, 460, 1025, 620)
        arrow(560, 620, 560, 450)
    else:
        box(80, 180, 310, 190, "1. Validate input", ["soil moisture, pH, N/P/K", "timestamp and sensor quality"], "#eef7ee", "#4b7f52")
        box(470, 180, 310, 190, "2. Safety gates", ["critical soil missing -> hold", "rain, wind, pH, EC limits"], "#fff6df", "#a8791c")
        box(860, 180, 310, 190, "3. Policy inference", ["crop stage + weather", "water, N, P2O5, K2O targets"], "#f1edff", "#6c4ab6")
        box(1250, 180, 290, 190, "4. Human review", ["generate work order", "explicit execute command"], "#e9f3ff", "#235789")
        arrow(390, 275, 470, 275); arrow(780, 275, 860, 275); arrow(1170, 275, 1250, 275)
        box(280, 590, 310, 190, "5. Fertilizer channels", ["N, P, K relays", "flow pulses -> cumulative L"], "#ffecec", "#a43d3d")
        box(650, 590, 310, 190, "6. Interlock", ["all fertilizer pumps OFF", "then outlet pump ON"], "#ffecec", "#a43d3d")
        box(1020, 590, 310, 190, "7. Fault handling", ["no-flow timeout", "manual STOP ALL", "all four relays OFF"], "#f5f5f5", "#657786")
        arrow(1395, 370, 435, 590); arrow(590, 685, 650, 685); arrow(960, 685, 1020, 685)
        draw.text((390, 850), "Feedback: relay state, pulse totals, faults and online status return to the server", fill="#4a5568", font=small_font)
    image.save(path, dpi=(220, 220))


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_border(cell, **kwargs):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = tcPr.first_child_found_in("w:tcBorders")
    if tcBorders is None:
        tcBorders = OxmlElement("w:tcBorders")
        tcPr.append(tcBorders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        if edge in kwargs:
            tag = "w:%s" % edge
            element = tcBorders.find(qn(tag))
            if element is None:
                element = OxmlElement(tag)
                tcBorders.append(element)
            for key in ["val", "sz", "space", "color"]:
                if key in kwargs[edge]:
                    element.set(qn("w:%s" % key), str(kwargs[edge][key]))


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar"); fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText"); instrText.set(qn("xml:space"), "preserve"); instrText.text = " PAGE "
    fldChar2 = OxmlElement("w:fldChar"); fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1); run._r.append(instrText); run._r.append(fldChar2)


def add_line_numbers(section):
    sectPr = section._sectPr
    ln = OxmlElement("w:lnNumType")
    ln.set(qn("w:countBy"), "1")
    ln.set(qn("w:restart"), "newPage")
    sectPr.append(ln)


def setup_document(doc):
    sec = doc.sections[0]
    sec.page_width = Cm(21.0); sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.5); sec.bottom_margin = Cm(2.5)
    sec.left_margin = Cm(2.5); sec.right_margin = Cm(2.5)
    add_line_numbers(sec)
    add_page_number(sec.footer.paragraphs[0])
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Times New Roman"; normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    normal.paragraph_format.space_after = Pt(0)
    for name, size, bold in [("Title", 16, True), ("Heading 1", 12, True), ("Heading 2", 12, True), ("Heading 3", 11, True)]:
        st = styles[name]; st.font.name = "Times New Roman"; st._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        st.font.size = Pt(size); st.font.bold = bold; st.font.color.rgb = RGBColor(0, 0, 0)
        st.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
        st.paragraph_format.space_before = Pt(10 if name == "Heading 1" else 6); st.paragraph_format.space_after = Pt(0)
    cap = styles.add_style("CaptionText", WD_STYLE_TYPE.PARAGRAPH)
    cap.font.name = "Times New Roman"; cap.font.size = Pt(11); cap.font.bold = True; cap.font.color.rgb = RGBColor(0, 0, 0)
    cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER; cap.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE


def add_para(doc, text, bold_prefix=None, align=None):
    p = doc.add_paragraph()
    if align is not None: p.alignment = align
    if bold_prefix and text.startswith(bold_prefix):
        p.add_run(bold_prefix).bold = True; p.add_run(text[len(bold_prefix):])
    else:
        p.add_run(text)
    return p


def add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h; set_cell_shading(hdr[i], "D9EAF7"); hdr[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for run in hdr[i].paragraphs[0].runs: run.bold = True
        set_cell_border(hdr[i], top={"val":"single","sz":"6","color":"557A95"}, bottom={"val":"single","sz":"6","color":"557A95"}, left={"val":"single","sz":"6","color":"557A95"}, right={"val":"single","sz":"6","color":"557A95"})
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value); cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_border(cells[i], top={"val":"single","sz":"4","color":"AAB7C4"}, bottom={"val":"single","sz":"4","color":"AAB7C4"}, left={"val":"single","sz":"4","color":"AAB7C4"}, right={"val":"single","sz":"4","color":"AAB7C4"})
    if widths:
        for row in table.rows:
            for i, width in enumerate(widths): row.cells[i].width = Inches(width)
    doc.add_paragraph()
    return table


def build():
    fig1 = FIG_DIR / "figure1_architecture.png"; fig2 = FIG_DIR / "figure2_workflow.png"
    make_diagram(fig1, False); make_diagram(fig2, True)
    doc = Document(); setup_document(doc)

    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("A safety-gated edge-cloud fertigation system with flow-meter-closed-loop control and weather-aware policy distillation")
    r.bold = True; r.font.size = Pt(16); r.font.name = "Times New Roman"
    add_para(doc, "Li Tianhao1, Liu Jiangping1*", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "1 Inner Mongolia Agricultural University, Hohhot, Inner Mongolia, China", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "*Corresponding author: Liu Jiangping; E-mail: SkyhaoLi@163.com; Postal address: Inner Mongolia Agricultural University, Hohhot, China", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Draft for author completion: add local field-validation results and confirm the final affiliation details before submission.", align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    doc.add_heading("Highlights", level=1)
    for item in [
        "A single-probe edge-cloud system links sensing, inference and fertigation.",
        "ExtraTrees distils an explicit weather and crop-stage teacher policy.",
        "Independent flow-meter feedback stops N, P and K channels at target volume.",
        "A hard safety gate blocks irrigation when critical soil data are invalid.",
        "Held-out policy reproduction reached 93.17% irrigation decision accuracy.",
    ]: add_para(doc, "- " + item)

    doc.add_heading("Abstract", level=1)
    add_para(doc, "Small and medium farms need fertigation controllers that remain useful when sensors, networks and actuators are imperfect. This paper presents ZhiRun, an edge-cloud fertigation system that combines a Rockchip RK3506B edge node, a public-server inference service and an ESP32-S3 actuator node. A single soil probe supplies moisture, temperature, pH and measured N-P-K values; weather and rain observations are acquired through RS485 sensors and a weather service. The server implements a safety-gated multi-output policy distilled from an explicit crop-stage and weather teacher. An ExtraTrees regressor predicts irrigation water and N, P2O5 and K2O application targets for four crops. Critical soil fields are fail-closed, whereas selected non-critical weather fields can use a regional fallback. At the actuator, each fertilizer pump is paired with a pulse flow meter. The N, P and K channels stop independently when their targets are reached, and the mixing-tank outlet pump is interlocked until all fertilizer channels finish. The generated dataset contains 39,600 policy samples from 2015-2025 weather records; training uses 2015-2022, validation uses 2023, and the independent test uses 2024-2025. On the test split, water, N, P2O5 and K2O R2 values are 0.9805, 0.9230, 0.9254 and 0.9576, respectively, and irrigation decision accuracy is 0.9317. These results quantify reproduction of the teacher policy, not yield improvement. The system provides a reproducible baseline for safe automation, while local yield, water-productivity and fertigation-response trials remain necessary for agronomic validation.")
    add_para(doc, "Keywords: edge computing; ExtraTrees; fertigation; flow-meter feedback; missing-data safety; precision irrigation")

    doc.add_heading("1. Introduction", level=1)
    add_para(doc, "Agricultural water scarcity and rising fertilizer costs have increased interest in precision irrigation and fertigation. Conventional timer-based systems apply water and nutrients without jointly considering soil status, crop stage and short-term weather. Internet-of-Things (IoT) architectures improve observability, but many prototypes assume continuous connectivity, clean sensor values and independent protection circuits. These assumptions are difficult to maintain in small farms, where a single probe may be the only soil instrument and where pumps, relay boards and communication adapters are assembled incrementally.")
    add_para(doc, "Three practical gaps motivate this work. First, a useful controller must distinguish critical soil evidence from optional environmental evidence: missing soil moisture or nutrient measurements should prevent automatic irrigation, whereas a missing wind or forecast value should not necessarily disable monitoring. Second, nutrient dosing cannot be validated by relay state alone. A pump can be energized while a blocked tube, empty tank or wiring fault produces no delivered volume. Third, a cloud model must be integrated with deterministic edge safety logic so that a transient network or inference error cannot leave a pump energized indefinitely.")
    add_para(doc, "ZhiRun addresses these gaps through four contributions. (1) It provides a compact edge-cloud architecture in which the RK3506B performs sensor acquisition, local display and communication, while the server performs policy inference and work-order management. (2) It uses an ExtraTrees multi-output regressor to distil an auditable teacher policy based on crop stage, measured single-probe moisture, weather and nutrient budgets. (3) It introduces channel-level flow-meter closure and a sequential interlock: N, P and K fertilizer channels stop independently, and the outlet pump is enabled only after all fertilizer channels stop. (4) It implements a fail-closed data-quality gate and bounded actuator state machine for operation under missing data and imperfect networks. The evaluation therefore focuses on policy reproduction and control safety rather than claiming yield gains that are not supported by local field labels.")
    add_para(doc, "The remainder of the paper describes the hardware and data pipeline, the teacher-policy distillation method, safety and execution logic, held-out metrics, and limitations that must be addressed in future field trials.")

    doc.add_heading("2. Materials and methods", level=1)
    doc.add_heading("2.1. System architecture", level=2)
    add_para(doc, "The system follows a browser-server-edge-actuator chain: a browser communicates with the public server; the server exchanges data with the RK3506B over Wi-Fi or Ethernet; the RK3506B reads RS485/Modbus sensors and forwards commands over a USB/CH341 serial link; and the ESP32-S3 drives four relay inputs. The architecture deliberately keeps model inference on the server and keeps the edge node lightweight. The local LVGL display uses the same state fields as the web interface, allowing an operator to inspect sensor values and pump states without opening the browser.")
    add_para(doc, "[Figure 1 near here]")

    doc.add_heading("2.2. Sensors, inputs and actuation", level=2)
    add_para(doc, "The implemented input schema contains air temperature and relative humidity, CO2, light, a single soil-moisture probe, soil temperature, soil pH, measured soil N/P/K, wind speed, rain amount, crop and date, and weather-derived variables. The probe is intentionally treated as a single representative soil measurement; the implementation does not assume 20, 40 or 60 cm layered probes. Weather history is sourced from NASA POWER daily data and short-range forecasts from a weather provider, with a regional cached fallback for selected unavailable fields. A local tipping-bucket rain sensor can be read as a pulse input and converted with a configurable millimetres-per-tip scale.")
    table1_headers = ["Subsystem", "Implemented component", "Role in the control loop"]
    table1_rows = [
        ["Edge computer", "RK3506B", "RS485 acquisition, local HMI, uplink and command forwarding"],
        ["Actuator computer", "ESP32-S3", "Relay state machine, pulse counting, timeout and stop-all"],
        ["Soil sensing", "Single probe", "Moisture, soil temperature, pH and measured N/P/K inputs"],
        ["Fertilizer channels", "Three pumps + three flow meters", "N, P and K dosing with independent volume closure"],
        ["Mixing and irrigation", "Outlet pump", "Starts only after fertilizer channels finish"],
        ["Connectivity", "Wi-Fi/Ethernet + USB/CH341", "Public-server access and edge-actuator link"],
    ]
    add_para(doc, "[Table 1 near here]")

    doc.add_heading("2.3. Teacher policy and crop calendar", level=2)
    add_para(doc, "The model is a policy-reproduction model rather than a yield model. The teacher policy is deterministic and combines a measured soil-moisture trigger, crop-stage nutrient budgets, crop coefficients, recent evapotranspiration and rain, and safety constraints. Four crop calendars are currently configured: potato, sugar beet, maize and sunflower. The date is converted to day of year, and the active growth stage is selected from crop-specific intervals. The teacher outputs four targets per mu: irrigation water (m3 per mu), nitrogen (kg per mu), P2O5 (kg per mu) and K2O (kg per mu). Here, mu denotes the local Chinese land unit of 666.67 m2.")
    add_para(doc, "The weather features include daily mean, maximum and minimum temperature, relative humidity, wind, radiation, current rain, two-day forecast rain, seven-day rain, ET0, seven-day ET0, growing degree accumulation and dry days. ET0 is calculated using the FAO-56 Penman-Monteith formulation. Rain and wind are used both as model features and as operational safety signals. The teacher adjusts the moisture trigger within bounded limits under forecast rain or high evaporative demand, but it never overrides a hard safety block.")

    doc.add_heading("2.4. ExtraTrees policy distillation", level=2)
    add_para(doc, "A training sample is formed by combining a weather day, a crop and stage, one soil profile, and two reproducible single-probe scenarios. The generated dataset contains 39,600 rows. Categorical features are crop, stage and three nutrient-level categories; numerical features cover weather, soil, crop-stage budgets, geography and sensor context. The data are split chronologically to test temporal transfer: 2015-2022 for training, 2023 for validation, and 2024-2025 for independent testing. This split prevents random mixing of adjacent days across train and test.")
    add_para(doc, "The estimator is an ExtraTreesRegressor with 350 trees, minimum leaf size 2, max_features 0.85, and a fixed random seed of 42. A preprocessing pipeline one-hot encodes categorical variables and passes numerical variables through unchanged. Predictions are clipped at zero. Irrigation decision accuracy is calculated by thresholding water at 0.5 m3 per mu and comparing the predicted and teacher binary decisions. MAE, RMSE and R2 are reported for each continuous target.")

    doc.add_heading("2.5. Missing-data safety gate", level=2)
    add_para(doc, "The runtime separates critical and non-critical fields. Automatic irrigation is held when soil moisture, soil pH or measured soil N/P/K are missing, stale or invalid. A forecast or wind field can instead be replaced by a regional weather fallback, and the decision is annotated with its data source. This design makes the system conservative about direct soil evidence while allowing continued monitoring and limited decision support when optional context is unavailable. A human must explicitly review and execute a generated work order; model inference alone does not energize the pumps.")

    doc.add_heading("2.6. Flow-meter-closed-loop execution", level=2)
    add_para(doc, "The ESP32-S3 maps relay inputs to N, P, K and the mixing-tank outlet pump. Each fertilizer pump is associated with one pulse flow meter. During a work order, pulse totals are converted to litres using a configurable pulses-per-litre calibration. When a channel reaches its target volume, only that channel is stopped. The outlet pump is inhibited while any fertilizer channel is active and is enabled only after all three channels report completion. A no-flow timeout, actuator fault, stale command or manual STOP ALL command de-energizes all four relays. Each channel also has a maximum run-time bound. These rules are implemented in the controller state machine and exposed through status feedback to the server and dashboard.")
    add_para(doc, "[Figure 2 near here]")

    doc.add_heading("2.7. Evaluation protocol", level=2)
    add_para(doc, "Evaluation uses the stored model metrics JSON and the generated policy sample file. The independent test set contains 7,200 rows from 2024-2025. Continuous predictions are evaluated with MAE, RMSE and R2. The controller logic is evaluated by deterministic simulations and unit tests for independent channel stopping, outlet-pump interlocking, no-flow timeout and stop-all behavior. Because the current project does not yet contain paired local treatment-yield observations, no claim is made about yield, water productivity, fertilizer recovery or economic return.")

    doc.add_heading("3. Results", level=1)
    doc.add_heading("3.1. Dataset and held-out policy performance", level=2)
    add_para(doc, "The chronological split produced 28,800 training rows, 3,600 validation rows and 7,200 independent test rows. Validation performance was stronger than the later-year test performance for every target, which is expected when the test period contains weather and scenario combinations not seen during fitting. The independent test results are summarized in Table 2.")
    table2_headers = ["Target", "MAE", "RMSE", "R2"]
    table2_rows = [
        ["Irrigation water (m3 mu-1)", "0.4599", "1.5792", "0.9805"],
        ["N (kg mu-1)", "0.0249", "0.1286", "0.9230"],
        ["P2O5 (kg mu-1)", "0.0148", "0.0755", "0.9254"],
        ["K2O (kg mu-1)", "0.0123", "0.0666", "0.9576"],
        ["Irrigation decision accuracy", "-", "-", "0.9317"],
    ]
    add_para(doc, "[Table 2 near here]")
    add_para(doc, "The water target achieved an R2 of 0.9805 and the nutrient targets achieved R2 values between 0.9230 and 0.9576. The binary irrigation decision accuracy was 0.9317. These scores indicate that the fitted model reproduces the generated teacher decisions well over the held-out years. They should not be interpreted as sensor-to-yield accuracy or as evidence that the teacher policy is agronomically optimal.")

    doc.add_heading("3.2. Runtime behavior under incomplete context", level=2)
    add_para(doc, "The runtime path preserves operation when non-critical environmental context is unavailable. For example, missing wind or forecast values can be replaced by a regional cached weather record and marked in the decision explanation. In contrast, missing or invalid soil moisture, pH or measured N/P/K holds automatic irrigation. This asymmetric policy prevents a visually complete but soil-unsupported decision. The generated work order remains a reviewable recommendation until the operator presses the explicit execution action.")

    doc.add_heading("3.3. Actuation and interlock behavior", level=2)
    add_para(doc, "Controller simulations and unit tests confirm four safety properties. First, each fertilizer channel can stop independently when its own flow-meter target is met. Second, a completed N channel does not stop P or K prematurely. Third, the outlet pump cannot start while a fertilizer relay remains active. Fourth, no-flow timeout, stale command or stop-all transitions all set the four relay outputs to OFF. This provides a deterministic boundary between a server recommendation and physical actuation.")
    table3_headers = ["Condition", "Required response", "Reason"]
    table3_rows = [
        ["Critical soil field absent or invalid", "Hold automatic irrigation", "Avoid decisions without direct soil evidence"],
        ["No flow while a fertilizer pump is ON", "Stop all relays and report fault", "Detect blocked line, empty tank or wiring fault"],
        ["One nutrient target reached", "Stop only that nutrient pump", "Preserve independent N/P/K dosing"],
        ["Any fertilizer channel active", "Keep outlet pump OFF", "Prevent unsequenced mixing and discharge"],
        ["Manual STOP ALL or stale command", "Turn all four relays OFF", "Provide immediate physical fail-safe"],
    ]
    add_para(doc, "[Table 3 near here]")

    doc.add_heading("4. Discussion", level=1)
    doc.add_heading("4.1. What is innovative in the present system", level=2)
    add_para(doc, "The main contribution is the integration boundary rather than a claim of a new agronomic optimum. The system explicitly assigns different responsibilities to the cloud and edge: the cloud can host a heavier policy model and weather processing, while the edge preserves local visibility and deterministic actuator protection. The single-probe assumption is also explicit. Instead of fabricating layered soil states, the decision path uses the measurements that the deployed hardware can actually provide. This makes the system easier to audit and calibrate in a small farm.")
    add_para(doc, "The second contribution is the combination of policy distillation and safety gates. A transparent teacher policy can be inspected by agronomists, while ExtraTrees provides a compact nonlinear approximation that is practical to serve. The model is never allowed to bypass hard gates. The third contribution is volume-based fertigation closure: relay state is not treated as delivered dose; flow pulses are used to terminate each nutrient channel, and the outlet pump is interlocked with channel completion. This directly addresses a failure mode that is common in timer-only prototypes.")

    doc.add_heading("4.2. Relation to prior work", level=2)
    add_para(doc, "The architecture follows the broader IoT agriculture direction in which distributed sensing, connectivity and analytics support site-specific management. It differs from many proof-of-concept systems by treating data quality and actuation sequencing as first-class control variables. The use of FAO-56 ET0 provides a recognized physical weather feature, but ET0 is used as context for a teacher policy and not as a substitute for local soil-water calibration. The ExtraTrees choice is consistent with its ability to model nonlinear interactions and mixed feature sets without requiring a large neural inference stack at the edge.")

    doc.add_heading("4.3. Limitations and required next experiments", level=2)
    add_para(doc, "The most important limitation is label origin. The 39,600 samples are generated from regional historical weather, published crop-stage priors and single-probe setpoints. The model therefore reproduces a rule-based teacher; it has not learned a local yield response. The present evaluation also lacks randomized field treatments, calibrated flow-meter uncertainty, pump energy measurements, communication latency distributions and multi-season crop-quality labels. The project should next collect synchronized soil, flow, weather, management and harvest records; recalibrate each meter in situ; and compare the policy against timer irrigation and an agronomist baseline across independent plots. A future multi-objective model can then optimize yield, water productivity, nutrient use and energy under explicit constraints.")
    add_para(doc, "Operationally, the public-server dependency is a design trade-off. It simplifies model updates and allows a larger runtime environment, but it creates a need for a robust offline state and clear operator feedback. The current fail-closed soil gate and bounded relay state machine reduce physical risk, yet a production deployment should add local command expiry, signed firmware, authenticated transport, event persistence and an independent hardwired emergency stop.")

    doc.add_heading("5. Conclusions", level=1)
    add_para(doc, "This paper presented ZhiRun, a safety-gated edge-cloud fertigation system built for a single soil probe and four-pump hardware configuration. The system combines RS485 sensing, server-side ExtraTrees policy distillation, asymmetric missing-data handling, independent flow-meter closure for N/P/K dosing, and an outlet-pump interlock. On a chronological independent test set, the policy reproduction model achieved R2 values of 0.9805 for water, 0.9230 for N, 0.9254 for P2O5 and 0.9576 for K2O, with 93.17% irrigation decision accuracy. These metrics establish a reproducible software and control baseline. They do not establish agronomic or economic superiority. Local field trials with measured yield, quality, water and nutrient outcomes are required before the system can be promoted as an optimized automatic irrigation strategy.")

    doc.add_heading("Declarations", level=1)
    add_para(doc, "Availability of data and materials. The project source code, model configuration, metric JSON and the exported dataset summary are maintained in the ZhiRun repository: https://github.com/isa-1211/ZhiRun. The authors should add a tagged release and persistent archive link before submission. Weather data provenance and preprocessing scripts are included in the repository.")
    add_para(doc, "Competing interests. The authors declare that they have no competing interests.")
    add_para(doc, "Funding. Not applicable at the draft stage. Add grant number and funding agency if applicable.")
    add_para(doc, "Authors' contributions. Li Tianhao conceived the system, implemented the software and prepared the manuscript. Liu Jiangping supervised the study, reviewed the methodology and revised the manuscript. Both authors approved the submitted version.")
    add_para(doc, "Acknowledgments. Not applicable at the draft stage. Add field, laboratory or open-source acknowledgments after permissions are obtained.")
    add_para(doc, "Declaration of generative artificial intelligence and artificial intelligence-assisted technologies in the writing process. During preparation of this draft, generative AI assistance was used for language drafting and structural editing. The authors reviewed the technical content, source data, figures and references and remain responsible for the final manuscript.")

    doc.add_heading("References", level=1)
    refs = [
        "Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). Crop evapotranspiration - Guidelines for computing crop water requirements - FAO Irrigation and drainage paper 56. FAO, Rome. https://www.fao.org/4/X0490E/X0490E00.htm",
        "Ayaz, M., Ammad-Uddin, M., Sharif, Z., Mansour, A., Aggoune, E.H.M. (2019). Internet-of-Things (IoT)-based smart agriculture: Toward making the fields talk. IEEE Access, 7, 129551-129583. https://doi.org/10.1109/ACCESS.2019.2932609",
        "Fereres, E., Soriano, M.A. (2007). Deficit irrigation for reducing agricultural water use. Journal of Experimental Botany, 58(2), 147-159. https://doi.org/10.1093/jxb/erl165",
        "Geurts, P., Ernst, D., Wehenkel, L. (2006). Extremely randomized trees. Machine Learning, 63, 3-42. https://doi.org/10.1007/s10994-006-6226-1",
        "NASA Langley Research Center (2024). NASA POWER project documentation. https://power.larc.nasa.gov/docs/",
        "Poggio, L., de Sousa, L.M., Batjes, N.H., et al. (2021). SoilGrids 2.0: producing soil information for the globe with quantified spatial uncertainty. SOIL, 7, 217-240. https://doi.org/10.5194/soil-7-217-2021",
        "ZhiRun project contributors (2026). ZhiRun smart agricultural water-fertilizer integration system: source code, model configuration and controller tests. GitHub repository. https://github.com/isa-1211/ZhiRun",
    ]
    for ref in refs: add_para(doc, ref)

    doc.add_heading("Tables", level=1)
    doc.add_paragraph("Table 1. Main hardware and software responsibilities.", style="CaptionText")
    add_table(doc, table1_headers, table1_rows)
    doc.add_paragraph("Table 2. Independent test performance for policy reproduction (2024-2025; n = 7,200).", style="CaptionText")
    add_table(doc, table2_headers, table2_rows)
    doc.add_paragraph("Table 3. Safety and interlock rules verified in the controller implementation.", style="CaptionText")
    add_table(doc, table3_headers, table3_rows)
    doc.add_heading("Figures", level=1)
    add_para(doc, "Figure 1. ZhiRun edge-cloud hardware and data architecture.")
    doc.add_picture(str(fig1), width=Inches(6.2))
    add_para(doc, "Figure 2. Decision, review, execution and fail-safe feedback sequence.")
    doc.add_picture(str(fig2), width=Inches(6.2))

    doc.core_properties.title = "ZhiRun safety-gated edge-cloud fertigation system"
    doc.core_properties.subject = "Original research article draft for Journal of Agricultural Engineering"
    doc.core_properties.author = "Li Tianhao; Liu Jiangping"
    doc.core_properties.keywords = "fertigation; edge computing; ExtraTrees; flow-meter feedback; safety gate"
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build()
