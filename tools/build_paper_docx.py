import csv
import json
import re
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
OUT_SUPP = ROOT / "论文补充材料_Journal_of_Agricultural_Engineering.docx"
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
    table.autofit = True
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    repeat_header = OxmlElement("w:tblHeader")
    repeat_header.set(qn("w:val"), "true")
    header_properties.append(repeat_header)
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
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.name = "Times New Roman"
                    run.font.size = Pt(9)
    doc.add_paragraph()
    return table


def build():
    # Publication figures are generated from the repository data by
    # build_paper_figures.py. The legacy hand-drawn diagrams are retained only
    # as source history and are not embedded in the manuscript.
    fig_arch = FIG_DIR / "fig4_architecture.png"
    fig_data = FIG_DIR / "fig1_dataset_overview.png"
    fig_parity = FIG_DIR / "fig2_test_parity.png"
    fig_gen = FIG_DIR / "fig3_generalization.png"
    fig_ctrl = FIG_DIR / "fig5_controller_trace.png"
    fig_unc = FIG_DIR / "fig6_bootstrap_uncertainty.png"
    fig_mc = FIG_DIR / "fig7_controller_monte_carlo.png"
    fig_iterations = FIG_DIR / "fig9_iteration_learning_curve.png"
    iteration_metrics = json.loads((FIG_DIR / "iteration_experiment_metrics.json").read_text(encoding="utf-8"))
    with (FIG_DIR / "iteration_learning_curve_raw.csv").open(encoding="utf-8-sig", newline="") as handle:
        iteration_raw = list(csv.DictReader(handle))
    doc = Document(); setup_document(doc)

    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("A safety-gated edge-cloud fertigation system with flow-meter-closed-loop control and weather-aware policy distillation")
    r.bold = True; r.font.size = Pt(16); r.font.name = "Times New Roman"
    add_para(doc, "Li Tianhao1, Liu Jiangping1*", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "1 Inner Mongolia Agricultural University, Hohhot, Inner Mongolia, China", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "*Corresponding author: Liu Jiangping; E-mail: SkyhaoLi@163.com; Postal address: Inner Mongolia Agricultural University, Hohhot, China", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Draft for author completion: add local field-validation results and confirm the final affiliation details before submission.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Keywords: edge computing; ExtraTrees; fertigation; flow-meter feedback; missing-data safety; precision irrigation", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Availability of data and materials. Source code, model configuration, metric JSON, experiment scripts and the exported dataset summary are maintained in the ZhiRun repository: https://github.com/isa-1211/ZhiRun.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Competing interests. The authors declare that they have no competing interests.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Funding. No specific funding is reported for this study.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Authors' contributions. Li Tianhao conceived the system, implemented the software and prepared the manuscript. Liu Jiangping supervised the study, reviewed the methodology and revised the manuscript. Both authors approved the submitted version.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Acknowledgments. The authors acknowledge the NASA Prediction Of Worldwide Energy Resources (POWER) Project for providing the public meteorological data used in the reproducible policy dataset. No individual contributor outside the listed authors is named in this draft.", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "Declaration of generative artificial intelligence and artificial intelligence-assisted technologies in the writing process. Generative AI assistance was used for language drafting and structural editing. The authors reviewed the technical content, source data, figures and references and remain responsible for the final manuscript.", align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    doc.add_heading("Abstract", level=1)
    add_para(doc, "Small and medium farms need fertigation controllers that remain useful when sensors, networks and actuators are imperfect. This paper presents ZhiRun, an edge-cloud fertigation system that combines a Rockchip RK3506B edge node, a public-server inference service and an ESP32-S3 actuator node. A single soil probe supplies moisture, temperature, pH and measured N-P-K values; weather and rain observations are acquired through RS485 sensors and a weather service. The server implements a safety-gated multi-output policy distilled from an explicit crop-stage and weather teacher. An ExtraTrees regressor predicts irrigation water and N, P2O5 and K2O application targets for four crops. Critical soil fields are fail-closed, whereas selected non-critical weather fields can use a regional fallback. At the actuator, each fertilizer pump is paired with a pulse flow meter. The N, P and K channels stop independently when their targets are reached, and the mixing-tank outlet pump is interlocked until all fertilizer channels finish. The generated dataset contains 39,600 policy samples from 2015-2025 weather records; training uses 2015-2022, validation uses 2023, and the independent test uses 2024-2025. On the test split, water, N, P2O5 and K2O R2 values are 0.9805, 0.9230, 0.9254 and 0.9576, respectively, and irrigation decision accuracy is 0.9317. Across 25 validation learning-curve runs, increasing training rows from 2,880 to 28,800 raised mean decision accuracy from 0.8666 to 0.9332 and reduced mean water MAE from 0.7593 to 0.3463 m3 mu-1. Removing weather or soil features reduced decision accuracy to 0.8003 and 0.5932, while a transparent soil-moisture threshold reference achieved 0.8194. Controller and server regression suites passed 35 tests, and 14,000 controller simulations produced no outlet-fertilizer overlap. These results quantify reproduction of the teacher policy and software safety, not yield improvement. Local yield, water-productivity and fertigation-response trials remain necessary for agronomic validation.")
    add_para(doc, "Keywords: edge computing; ExtraTrees; fertigation; flow-meter feedback; missing-data safety; precision irrigation")

    doc.add_heading("1. Introduction", level=1)
    add_para(doc, "Agricultural water scarcity and rising fertilizer costs have increased interest in precision irrigation and fertigation. Conventional timer-based systems apply water and nutrients without jointly considering soil status, crop stage and short-term weather. Internet-of-Things (IoT) architectures improve observability and data-driven management (Wolfert et al., 2017; Ayaz et al., 2019; Khanna and Kaur, 2019; Saiz-Rubio and Rovira-Mas, 2020), but many prototypes assume continuous connectivity, clean sensor values and independent protection circuits. These assumptions are difficult to maintain in small farms, where a single probe may be the only soil instrument and where pumps, relay boards and communication adapters are assembled incrementally.")
    add_para(doc, "Irrigation scheduling methods range from crop-water calculations and direct soil or plant sensing to machine-learning prediction (Jones, 2004; Fereres and Soriano, 2007; Vellidis et al., 2008; Jimenez et al., 2021). Machine learning has expanded the range of agricultural prediction and decision-support tasks, but review studies consistently identify data quality, generalization and deployment as practical limitations (Kamilaris and Prenafeta-Boldu, 2018; Liakos et al., 2018; Benos et al., 2021). Three practical gaps therefore motivate this work. First, a useful controller must distinguish critical soil evidence from optional environmental evidence: missing soil moisture or nutrient measurements should prevent automatic irrigation, whereas a missing wind or forecast value should not necessarily disable monitoring. Second, nutrient dosing cannot be validated by relay state alone. A pump can be energized while a blocked tube, empty tank or wiring fault produces no delivered volume. Third, a cloud model must be integrated with deterministic edge safety logic so that a transient network or inference error cannot leave a pump energized indefinitely.")
    add_para(doc, "ZhiRun addresses these gaps through four contributions. (1) It provides a compact edge-cloud architecture in which the RK3506B performs sensor acquisition, local display and communication, while the server performs policy inference and work-order management. (2) It uses an ExtraTrees multi-output regressor (Geurts et al., 2006) to distil an auditable teacher policy based on crop stage, measured single-probe moisture, weather and nutrient budgets. (3) It introduces channel-level flow-meter closure and a sequential interlock: N, P and K fertilizer channels stop independently, and the outlet pump is enabled only after all fertilizer channels stop. (4) It implements a fail-closed data-quality gate and bounded actuator state machine for operation under missing data and imperfect networks. The evaluation therefore focuses on policy reproduction and control safety rather than claiming yield gains that are not supported by local field labels.")
    add_para(doc, "The remainder of the paper describes the hardware and data pipeline, the teacher-policy distillation method, safety and execution logic, held-out metrics, and limitations that must be addressed in future field trials.")

    doc.add_heading("2. Materials and methods", level=1)
    doc.add_heading("2.1. System architecture", level=2)
    add_para(doc, "The system follows a browser-server-edge-actuator chain: a browser communicates with the public server; the server exchanges data with the RK3506B over Wi-Fi or Ethernet; the RK3506B reads RS485/Modbus sensors and forwards commands over a USB/CH341 serial link; and the ESP32-S3 drives four relay inputs (Figure 1; Table 1). This separation follows the edge-computing principle of placing latency- and safety-sensitive functions close to the physical process while retaining server resources for analytics (Shi et al., 2016). The architecture deliberately keeps model inference on the server and keeps the edge node lightweight. The local LVGL display uses the same state fields as the web interface, allowing an operator to inspect sensor values and pump states without opening the browser.")
    add_para(doc, "A normal data cycle begins with a timestamped sensor snapshot at the edge. The collector validates register values, normalizes units and publishes the snapshot to the server. The server stores the latest snapshot, enriches it with date-derived crop stage and weather features, and returns a decision object containing targets, alerts, data-quality flags and an explanatory status. The scheduler runs once each day at 12:00 server local time. When automatic execution is enabled, only a decision marked ready by the soil-data and weather safety gates is converted into a bounded command; manual generation, execution and STOP ALL remain available. The server forwards an accepted command to the RK3506B, which relays it to the ESP32-S3. Status messages travel in the reverse direction and include state, cumulative pulse totals, active outputs, elapsed time and faults. This separation allows the interface to remain informative when the actuator is offline and allows the actuator to stop safely when the server is unreachable.")
    add_para(doc, "The communication design also accommodates the user's observed network conditions. Wi-Fi is retried after link loss, Ethernet and Wi-Fi are treated as alternative uplinks, and the edge software does not rely on a fixed USB insertion order. The paper treats these features as availability mechanisms rather than as agronomic innovations. Their purpose is to keep the sensing and command path recoverable while ensuring that reconnect logic cannot silently restart a previous pump command.")
    add_para(doc, "[Figure 1 near here]")

    doc.add_heading("2.2. Sensors, inputs and actuation", level=2)
    add_para(doc, "The implemented input schema contains air temperature and relative humidity, CO2, light, a single soil-moisture probe, soil temperature, soil pH, measured soil N/P/K, wind speed, rain amount, crop and date, and weather-derived variables. The probe is intentionally treated as a single representative soil measurement; the implementation does not assume 20, 40 or 60 cm layered probes. Weather history is sourced from NASA POWER daily data (NASA Langley Research Center, 2024) and short-range forecasts from a weather provider, with a regional cached fallback for selected unavailable fields. A local tipping-bucket rain sensor can be read as a pulse input and converted with a configurable millimetres-per-tip scale.")
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
    add_para(doc, "The weather features include daily mean, maximum and minimum temperature, relative humidity, wind, radiation, current rain, two-day forecast rain, seven-day rain, ET0, seven-day ET0, growing degree accumulation and dry days. ET0 is calculated using the FAO-56 Penman-Monteith formulation (Allen et al., 1998). Rain and wind are used both as model features and as operational safety signals. The teacher adjusts the moisture trigger within bounded limits under forecast rain or high evaporative demand, but it never overrides a hard safety block.")
    add_para(doc, "For a valid soil snapshot, the water deficit is computed from the difference between the measured probe moisture and the stage trigger/target pair. The implementation converts this percentage deficit through a stage-specific effective depth and an application-efficiency factor, then clips the result at the configured maximum single-event volume. In simplified form, moisture deficit is max(0, target - measured moisture) multiplied by effective depth and converted to millimetres; gross application is the larger of this deficit and the ET-driven need divided by application efficiency; and the final water target is gross application multiplied by the local mu conversion and bounded by the single-event limit. Fertilizer targets are calculated from the remaining stage budgets and the soil nutrient level factors low = 1.0, medium = 0.65 and high = 0.0. Fertilization is due only when irrigation is positive, the interval since the previous event is ready, and the EC safety gate is open. The maximum per-event nutrient rates are 2.5 kg mu-1 for N, 1.5 kg mu-1 for P2O5 and 3.0 kg mu-1 for K2O.")
    add_para(doc, "This explicit teacher formulation is important for interpretation. ExtraTrees is not discovering a causal crop response from harvest labels; it is approximating a constrained decision surface. The model can smooth nonlinear interactions between crop, stage, weather and sensor context, but it cannot correct a biased teacher policy. The architecture therefore exposes the teacher assumptions and retains them as auditable configuration rather than hiding them in an opaque end-to-end neural model.")

    doc.add_heading("2.4. ExtraTrees policy distillation", level=2)
    add_para(doc, "A training sample is formed by combining a weather day, a crop and stage, one soil profile, and two reproducible single-probe scenarios. The generated dataset contains 39,600 rows. Categorical features are crop, stage and three nutrient-level categories; numerical features cover weather, soil, crop-stage budgets, geography and sensor context. The data are split chronologically to test temporal transfer: 2015-2022 for training, 2023 for validation, and 2024-2025 for independent testing. This split prevents random mixing of adjacent days across train and test.")
    add_para(doc, "The estimator is an ExtraTreesRegressor, a randomized tree ensemble in the lineage of random forests (Breiman, 2001; Geurts et al., 2006), implemented with scikit-learn (Pedregosa et al., 2011). It uses 350 trees, minimum leaf size 2, max_features 0.85, and a fixed random seed of 42. A preprocessing pipeline one-hot encodes categorical variables and passes numerical variables through unchanged. Predictions are clipped at zero. Irrigation decision accuracy is calculated by thresholding water at 0.5 m3 per mu and comparing the predicted and teacher binary decisions. MAE, RMSE and R2 are reported for each continuous target.")

    doc.add_heading("2.5. Missing-data safety gate", level=2)
    add_para(doc, "The runtime separates critical and non-critical fields. Automatic irrigation is held when soil moisture, soil pH or measured soil N/P/K are missing, stale or invalid. A forecast or wind field can instead be replaced by a regional weather fallback, and the decision is annotated with its data source. This design makes the system conservative about direct soil evidence while allowing continued monitoring and limited decision support when optional context is unavailable. Model inference alone does not energize a pump unless the automatic-execution option is enabled, the result is executable, a current device is online and the bounded command is accepted; the manual path uses the same gates.")

    doc.add_heading("2.6. Flow-meter-closed-loop execution", level=2)
    add_para(doc, "The ESP32-S3 maps relay inputs to N, P, K and the mixing-tank outlet pump. Each fertilizer pump is associated with one pulse flow meter. During a work order, pulse totals are converted to litres using a configurable pulses-per-litre calibration. When a channel reaches its target volume, only that channel is stopped. The outlet pump is inhibited while any fertilizer channel is active and is enabled only after all three channels report completion. A no-flow timeout, actuator fault, stale command or manual STOP ALL command de-energizes all four relays. Each channel also has a maximum run-time bound. These rules are implemented in the controller state machine, summarized in Table 3 and exposed through status feedback to the server and dashboard.")
    add_para(doc, "The flow-meter closure is evaluated against a baseline captured when the controller starts, rather than against an absolute lifetime counter. This prevents historical pulses from satisfying a new work order. The controller accepts either line identifiers or nutrient identifiers in a sensor frame, which permits the RK3506B adapter to preserve the physical A/B/C wiring names while the server reports the agronomic N/P/K names. The default calibration is 450 pulses per litre, but the configuration explicitly warns that every meter must be calibrated with a measured container. In a production experiment, calibration error should be reported as a dose uncertainty and not silently absorbed into the machine-learning error.")
    doc.add_heading("2.7. Evaluation protocol", level=2)
    add_para(doc, "Evaluation uses the stored model metrics JSON and the generated policy sample file. The independent test set contains 7,200 rows from 2024-2025. Continuous predictions are evaluated with MAE, RMSE and R2. The controller logic is evaluated by deterministic simulations and unit tests for independent channel stopping, outlet-pump interlocking, no-flow timeout and stop-all behavior. Because the current project does not yet contain paired local treatment-yield observations, no claim is made about yield, water productivity, fertilizer recovery or economic return.")
    add_para(doc, "For each continuous target, MAE measures the average absolute deviation in the native target unit, RMSE emphasizes larger deviations, and R2 measures variance explained relative to the test-set mean. The binary irrigation decision threshold is 0.5 m3 mu-1, chosen to separate a zero/hold decision from a positive irrigation recommendation in the teacher policy. The baseline comparison uses exactly the same rows and target units as the fitted model. Sampling uncertainty is estimated with the non-parametric bootstrap (Efron, 1979). No test-time tuning is applied. Reported values are rounded to four decimal places only after calculation.")
    doc.add_heading("2.8. Data quality and reproducibility", level=2)
    add_para(doc, "The weather file is parsed by date, sentinel values are converted to missing values, and gaps of up to three days are linearly interpolated before feature construction. The sample generator fixes the NumPy random seed at 42. Soil scenarios are bounded to 2-95% during generation, while runtime validation accepts only a measured moisture value in the physical 0-100% range. All feature names, target names, split years, model hyperparameters and metric calculations are stored in the training script and the model metrics JSON. This makes the reported numbers reproducible from the repository without access to the live farm device.")
    doc.add_heading("2.9. Baselines and robustness analysis", level=2)
    add_para(doc, "Two simple baselines were calculated on the same independent test set. The zero-output baseline always returns zero water and nutrients, representing a controller that never irrigates. The training-mean baseline returns the mean target vector calculated from 2015-2022. These baselines are intentionally weak but provide a transparent reference for policy reproduction. We additionally report performance separately for 2024 and 2025 and separately for each crop to expose temporal and crop-specific variation rather than hiding it in one aggregate score.")
    doc.add_heading("2.10. Feature ablation and transparent agronomic reference", level=2)
    add_para(doc, "To test whether the model depends on the information sources claimed by the architecture, we retrained the same ExtraTrees configuration after removing three feature groups: all weather variables, all soil variables, or all crop-stage and nutrient-budget variables. The ablations use the identical chronological split, target definitions and random seed as the full model. We also evaluated a transparent soil-moisture reference that irrigates when measured moisture is below the stage trigger and assigns the mean positive water event; it never predicts fertilizer. These experiments quantify sensitivity of teacher-policy reproduction and are not evidence of crop response or treatment superiority.")
    doc.add_heading("2.11. Decision-threshold sensitivity and software regression", level=2)
    add_para(doc, "Operational irrigation classification was evaluated against the fixed teacher event definition of at least 0.5 m3 mu-1. The model-output threshold was varied from 0.1 to 2.0 m3 mu-1, and true-positive, false-positive, false-negative and true-negative counts were reported with precision, recall, F1 and specificity. This one-factor analysis tests sensitivity to the execution threshold without refitting the model. In addition, the current single-probe model/controller suite and server quality-gate suite were executed separately from their respective project roots to preserve their import paths.")
    doc.add_heading("2.12. Repeated training and parameter sensitivity", level=2)
    add_para(doc, "A repeated learning-curve experiment was conducted to expose model iteration behavior rather than reporting only the selected final fit. The 2015-2022 training pool was sampled at 10%, 25%, 50%, 75% and 100%. Each size was trained with five predetermined seeds (11, 23, 37, 53 and 71), yielding 25 fits; the 2024-2025 test set remained fixed and untouched. Every fit used 350 trees, minimum leaf size 2 and max_features 0.85. A separate 12-run sensitivity grid used all 28,800 training rows and crossed 100, 200, 350 and 500 trees with minimum leaf sizes 1, 2 and 4 at seed 42. These 37 post hoc robustness runs evaluate sample-size and parameter sensitivity; they were not used to search the test set for a replacement production model.")

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
    add_para(doc, "[Figure 3 near here]")
    add_para(doc, "The water target achieved an R2 of 0.9805 and the nutrient targets achieved R2 values between 0.9230 and 0.9576 (Figure 3). The binary irrigation decision accuracy was 0.9317. These scores indicate that the fitted model reproduces the generated teacher decisions well over the held-out years. They should not be interpreted as sensor-to-yield accuracy or as evidence that the teacher policy is agronomically optimal.")
    add_para(doc, "The error scale is also informative. The water MAE of 0.4599 m3 mu-1 is small relative to the 25 m3 mu-1 maximum event configured in the teacher, whereas the nutrient MAEs are 0.0249, 0.0148 and 0.0123 kg mu-1 for N, P2O5 and K2O. Because most nutrient rows are zero, these aggregate errors should be read together with the non-zero rates in Table 4. A model can obtain a low average nutrient MAE by predicting zero too often; the decision comparison and the baseline table reduce this ambiguity but do not replace precision-recall analysis on positive fertigation events. That analysis is reserved for the future field-labeled dataset.")

    doc.add_heading("3.2. Runtime behavior under incomplete context", level=2)
    add_para(doc, "The runtime path preserves operation when non-critical environmental context is unavailable. For example, missing wind or forecast values can be replaced by a regional cached weather record and marked in the decision explanation. In contrast, missing or invalid soil moisture, pH or measured N/P/K holds automatic irrigation. This asymmetric policy prevents a visually complete but soil-unsupported decision. At 12:00 each day the scheduler can automatically generate and execute a ready work order, while the dashboard retains manual generation and execution for supervised use.")
    add_para(doc, "The distinction between a recommendation and an execution command remains explicit in the state machine. A newly inferred result is first represented as a proposed work order with target volumes and reasons. A separate server action, initiated either by the configured daily scheduler or by the operator, is required to change the controller from IDLE to DOSING or OUTLET_TRANSFER. If the board is offline, the server retains the recommendation and reports the communication state; it does not infer successful physical delivery from a successful inference response. Conversely, a relay status without flow pulses is classified as a fault rather than as completed fertigation. These semantics are designed to avoid the common dashboard failure mode in which a generated work order is mistaken for delivered water or nutrient.")

    doc.add_heading("3.3. Actuation and interlock behavior", level=2)
    add_para(doc, "Controller simulations and unit tests confirm four safety properties. First, each fertilizer channel can stop independently when its own flow-meter target is met. Second, a completed N channel does not stop P or K prematurely. Third, the outlet pump cannot start while a fertilizer relay remains active. Fourth, no-flow timeout, stale command or stop-all transitions all set the four relay outputs to OFF. This provides a deterministic boundary between a server recommendation and physical actuation.")
    add_para(doc, "The controller trace in Table 7 illustrates why this logic matters even for a small work order. The N/P/K target volumes are different because each channel has a different nutrient target and concentration. A single shared timer would either under-dose one channel or overrun another. Independent pulse totals allow the channels to finish at different times, while the outlet interlock guarantees that the mixing-tank pump starts only after the fertilizer phase has ended. The trace is intentionally small enough to reproduce on a bench without running a full field pump.")
    table3_headers = ["Condition", "Required response", "Reason"]
    table3_rows = [
        ["Critical soil field absent or invalid", "Hold automatic irrigation", "Avoid decisions without direct soil evidence"],
        ["No flow while a fertilizer pump is ON", "Stop all relays and report fault", "Detect blocked line, empty tank or wiring fault"],
        ["One nutrient target reached", "Stop only that nutrient pump", "Preserve independent N/P/K dosing"],
        ["Any fertilizer channel active", "Keep outlet pump OFF", "Prevent unsequenced mixing and discharge"],
        ["Manual STOP ALL or stale command", "Turn all four relays OFF", "Provide immediate physical fail-safe"],
    ]
    add_para(doc, "[Table 3 near here]")

    doc.add_heading("3.4. Dataset composition and target sparsity", level=2)
    add_para(doc, "The generated dataset contains 39,600 rows across 11 calendar years, with 3,600 rows per year (Figure 2; Table 4). Crop representation is slightly unbalanced because the configured calendars have different numbers of active days: sugar beet contributes 10,560 rows, maize 10,164, sunflower 9,768 and potato 9,108. Twelve stage labels are represented across the four crops, with the seedling stage contributing 11,946 rows and maturity and filling stages contributing the remainder. Only 33.95% of rows have a non-zero water target, which means that a controller that always irrigates would be operationally unsafe even if its average volume appeared plausible. Non-zero N, P2O5 and K2O targets occur in 8.90%, 6.96% and 5.08% of rows, respectively. The target distributions are therefore sparse and strongly right-skewed.")
    table4_headers = ["Variable", "Mean", "Standard deviation", "Non-zero rate"]
    table4_rows = [
        ["Water (m3 mu-1)", "8.3166", "11.6524", "33.95%"],
        ["N (kg mu-1)", "0.1263", "0.4690", "8.90%"],
        ["P2O5 (kg mu-1)", "0.0696", "0.2836", "6.96%"],
        ["K2O (kg mu-1)", "0.0635", "0.3437", "5.08%"],
        ["Single-probe moisture (%)", "42.1001", "15.2287", "100%"],
        ["Two-day rain forecast (mm)", "5.0114", "9.3350", "100%"],
        ["ET0 (mm d-1)", "4.6361", "1.4941", "100%"],
    ]
    add_para(doc, "[Table 4 near here]")
    add_para(doc, "[Figure 2 near here]")

    doc.add_heading("3.5. Temporal and crop-level generalization", level=2)
    add_para(doc, "The two independent test years show a modest but measurable temporal change (Figure 4; Table 5). Water R2 decreases from 0.9829 in 2024 to 0.9778 in 2025, while irrigation decision accuracy decreases from 0.9517 to 0.9117. The nutrient R2 values remain above 0.90 in both years, but K2O is more sensitive to the later-year distribution than water. At crop level, water decision accuracy ranges from 0.9239 for potato to 0.9375 for sugar beet. These differences are not large enough to support crop-specific claims of superiority, but they show why chronological and crop-stratified reporting is preferable to a single pooled score.")
    table5_headers = ["Subset", "Rows", "Water MAE", "Water R2", "N R2", "P2O5 R2", "K2O R2", "Decision accuracy"]
    table5_rows = [
        ["2024", "3,600", "0.4292", "0.9829", "0.9349", "0.9313", "0.9847", "0.9517"],
        ["2025", "3,600", "0.4905", "0.9778", "0.9090", "0.9182", "0.9226", "0.9117"],
        ["Sunflower", "1,776", "0.4474", "0.9794", "-", "-", "-", "0.9313"],
        ["Maize", "1,848", "0.4879", "0.9787", "-", "-", "-", "0.9329"],
        ["Sugar beet", "1,920", "0.4476", "0.9824", "-", "-", "-", "0.9375"],
        ["Potato", "1,656", "0.4562", "0.9811", "-", "-", "-", "0.9239"],
    ]
    add_para(doc, "[Table 5 near here]")
    add_para(doc, "[Figure 4 near here]")

    doc.add_heading("3.6. Comparison with transparent baselines", level=2)
    add_para(doc, "The proposed policy model substantially outperforms the two transparent baselines on the held-out years (Table 6). The zero-output baseline obtains a decision accuracy of 0.6953 because the generated policy is intentionally sparse, but its water MAE is 7.4469 m3 mu-1 and its water R2 is negative. The training-mean baseline has a decision accuracy of 0.3047 because it predicts a positive mean water target for every row, and its water MAE is 10.7181 m3 mu-1. In contrast, the ExtraTrees policy reaches 0.9317 decision accuracy and 0.4599 water MAE. This comparison supports the claim that the model learns the teacher policy structure rather than simply reproducing the dominant zero class.")
    table6_headers = ["Method", "Water MAE", "Water RMSE", "Water R2", "N MAE", "Decision accuracy"]
    table6_rows = [
        ["Zero-output baseline", "7.4469", "13.5368", "-0.4340", "0.1222", "0.6953"],
        ["Training-mean baseline", "10.7181", "11.3424", "-0.0068", "0.2257", "0.3047"],
        ["ExtraTrees policy (ours)", "0.4599", "1.5792", "0.9805", "0.0249", "0.9317"],
    ]
    add_para(doc, "[Table 6 near here]")

    doc.add_heading("3.7. Reproducible controller trace", level=2)
    add_para(doc, "A deterministic hardware-in-the-loop simulation was run with a 0.01 mu work order, a 1 L min-1 flow on each fertilizer channel and a 60 L min-1 outlet flow (Figure 5; Table 7). The generated targets were 0.25 L for N, 0.188 L for P and 0.225 L for K. All fertilizer channels were initially active; after the cumulative readings crossed their individual targets, the controller entered OUTLET_TRANSFER at 2 s and enabled the outlet pump. It completed the 250 s outlet phase at 252 s with no fault. This trace demonstrates sequencing and fail-safe state transitions; it is not a pump calibration or field throughput measurement.")
    table7_headers = ["Time", "Controller state", "Cumulative N/P/K (L)", "Active outputs", "Interpretation"]
    table7_rows = [
        ["0 s", "DOSING", "0.000 / 0.000 / 0.000", "N, P, K", "All requested fertilizer channels start"],
        ["2 s", "OUTLET_TRANSFER", "0.400 / 0.200 / 0.400", "Outlet", "Each fertilizer target reached; outlet interlock released"],
        ["252 s", "COMPLETE", "0.400 / 0.200 / 0.400", "None", "Outlet duration elapsed; all relays OFF"],
    ]
    add_para(doc, "[Table 7 near here]")

    table8_headers = ["Target", "MAE 95% CI", "RMSE 95% CI", "R2 95% CI"]
    table8_rows = [
        ["Water", "0.4262-0.4979", "1.4636-1.6990", "0.9774-0.9832"],
        ["N", "0.0220-0.0278", "0.1132-0.1439", "0.9065-0.9394"],
        ["P2O5", "0.0131-0.0166", "0.0663-0.0853", "0.9081-0.9420"],
        ["K2O", "0.0109-0.0138", "0.0511-0.0836", "0.9362-0.9751"],
    ]
    add_para(doc, "[Table 8 near here]")

    table9_headers = ["Event", "Positive n", "Precision", "Recall", "F1", "Balanced accuracy"]
    table9_rows = [
        ["Water > 0.5 m3 mu-1", "2,194", "0.8226", "1.0000", "0.9027", "0.9528"],
        ["N > 0.01 kg mu-1", "609", "0.3551", "1.0000", "0.5241", "0.9161"],
        ["P2O5 > 0.01 kg mu-1", "489", "0.3590", "1.0000", "0.5284", "0.9350"],
        ["K2O > 0.01 kg mu-1", "344", "0.2550", "1.0000", "0.4064", "0.9267"],
    ]
    add_para(doc, "[Table 9 near here]")

    table10_headers = ["Stress test", "Fields replaced", "Decision agreement", "Changed decisions", "Agreement with teacher"]
    table10_rows = [["Optional-weather masking", "wind, rain_today, rain_next_2d", "0.8526", "1,061 / 7,200", "0.7975"]]
    add_para(doc, "[Table 10 near here]")
    add_para(doc, "[Figure 5 near here]")

    doc.add_heading("3.8. Missing-data decision matrix", level=2)
    add_para(doc, "The missing-data policy was evaluated as a decision matrix rather than as a statistical imputation experiment. A valid soil moisture, pH and measured N/P/K set is required for automatic irrigation. If any of these critical fields is absent or stale, the server returns a hold decision and the interface explains that direct soil evidence is insufficient. Missing wind or forecast data can use a regional fallback record and are marked as fallback-derived. This asymmetry preserves monitoring and manual review without allowing the scheduler or operator to issue an unsupported automatic irrigation command. A future study should quantify the effect of each missingness pattern on decision utility using field-labeled data.")
    doc.add_heading("3.9. Sampling uncertainty and positive-event discrimination", level=2)
    add_para(doc, "Bootstrap resampling of the 7,200 independent test rows provides a sampling uncertainty check for the reported aggregate errors (Figure 6; Table 8). With 1,000 fixed-seed replicates, the water MAE 95% interval is 0.4262-0.4979 m3 mu-1 and the N, P2O5 and K2O MAE intervals are 0.0220-0.0278, 0.0131-0.0166 and 0.0109-0.0138 kg mu-1, respectively. The corresponding R2 intervals are 0.9774-0.9832, 0.9065-0.9394, 0.9081-0.9420 and 0.9362-0.9751. The narrow water interval reflects the large test set; the wider nutrient intervals reflect sparse positive events and their smaller effective sample sizes.")
    add_para(doc, "Positive-event metrics reveal a second aspect of performance (Table 9). Using a 0.5 m3 mu-1 water threshold and a 0.01 kg mu-1 nutrient threshold, water positive-event F1 is 0.9027. Nutrient F1 values are 0.5241 for N, 0.5284 for P2O5 and 0.4064 for K2O, with 609, 489 and 344 positive test rows. The model recalls every positive event under these thresholds in this teacher-generated test, but precision is lower because small positive predictions can be operationally counted as events. This is a reason to report the continuous errors and not present the 93.17% decision accuracy as the only result.")
    add_para(doc, "When wind, current rain and two-day rain forecast fields are replaced by their training-period medians as a stress test, 85.26% of irrigation decisions agree with the original model path and 1,061 of 7,200 decisions change. Agreement with the teacher decision falls to 79.75% (Supplementary Table S1). This result supports the need to retain a weather fallback and to expose the fallback flag to operators; it is not evidence that median imputation is an appropriate production forecast.")
    add_para(doc, "[Figure 6 near here]")

    doc.add_heading("3.10. Large-sample controller safety simulation", level=2)
    add_para(doc, "The controller implementation was evaluated in 14,000 reproducible random scenarios using seed 20260908. Ten thousand normal scenarios independently sampled N/P/K targets between 0.1 and 4.0 L, channel flows between 0.08 and 4.5 L min-1, and outlet durations between 3 and 30 s. Two thousand scenarios forced one randomly selected fertilizer channel to report zero flow, and a further 2,000 asserted an emergency stop during dosing. The simulation executed the production FlowController state transitions rather than a separate analytical approximation.")
    add_para(doc, "Of the 10,000 normal scenarios, 9,920 completed both dosing and outlet transfer. The remaining 80 combinations paired a sufficiently large target with a sufficiently low flow to exceed the configured 1,800 s dosing bound; the controller therefore entered FAULT and turned all outputs off. Across all normal scenarios, no outlet-fertilizer overlap and no premature fertilizer stop were observed, all terminal states had all outputs off, and the one-time-step overshoot stayed below the flow-dependent numerical bound. The largest observed cumulative overshoot was 0.0747 L. Every one of the 2,000 no-flow cases was detected and all four outputs were de-energized. The same 100% fault-and-off response was observed in the 2,000 emergency-stop cases (Supplementary Table S2 and Supplementary Figure S1).")
    add_para(doc, "These simulation results verify software invariants across a broad parameter space, but they do not substitute for physical calibration. Real flow meters introduce pulse quantization, electrical bounce, viscosity effects and pressure-dependent flow. The next hardware experiment should repeat the same scenario matrix using graduated vessels and record delivered-volume error, response latency and fault-detection time.")
    table11_headers = ["Scenario group", "n", "Primary outcome", "Observed result"]
    table11_rows = [
        ["Random normal work orders", "10,000", "Completed without safety timeout", "9,920 (99.2%)"],
        ["Random normal work orders", "10,000", "Outlet-fertilizer overlap", "0 (0.0%)"],
        ["Random normal work orders", "10,000", "Premature nutrient stop", "0 (0.0%)"],
        ["Random normal work orders", "10,000", "All outputs OFF at terminal state", "10,000 (100%)"],
        ["Single-channel no-flow fault", "2,000", "Fault detected and all outputs OFF", "2,000 (100%)"],
        ["Emergency stop during dosing", "2,000", "Fault detected and all outputs OFF", "2,000 (100%)"],
    ]
    add_para(doc, "[Table 11 near here]")
    add_para(doc, "[Figure 7 near here]")

    doc.add_heading("3.11. Feature ablation and transparent reference", level=2)
    add_para(doc, "The ablation study confirms that the apparent performance is not explained by a single hard-coded field. Removing weather variables increased water MAE from 0.4599 to 4.8725 m3 mu-1 and reduced irrigation decision accuracy from 93.17% to 80.03%. Removing soil variables had an even larger effect on the decision path: accuracy fell to 59.32% and water R2 fell to 0.8352. Removing crop-stage and nutrient-budget variables left water reproduction nearly unchanged (R2 0.9807) but reduced nutrient R2 to 0.8806, 0.8914 and 0.8850 for N, P2O5 and K2O. The result is consistent with the teacher design: weather and measured soil status dominate irrigation timing, whereas crop-stage budgets contribute more strongly to nutrient allocation.")
    add_para(doc, "The transparent soil-moisture threshold reference achieved 81.94% irrigation decision accuracy, water MAE 4.7158 m3 mu-1 and water R2 0.1417. Its nutrient R2 values were negative because it intentionally does not dose fertilizer. The full model therefore provides a materially better reproduction of the multi-output teacher than this single-threshold reference (Supplementary Table S3 and Supplementary Figure S2), but the comparison still remains within the synthetic policy-label setting. It does not establish that the learned policy saves water or increases yield relative to a farmer or timer controller.")
    ablation_headers = ["Method", "Removed or rule", "Water MAE", "Water R2", "N R2", "P2O5 R2", "K2O R2", "Decision accuracy"]
    ablation_rows = [
        ["Full ExtraTrees policy", "None", "0.4599", "0.9805", "0.9230", "0.9254", "0.9576", "0.9317"],
        ["No weather features", "15 weather variables", "4.8725", "0.4138", "0.6070", "0.6457", "0.5731", "0.8003"],
        ["No soil features", "9 soil variables", "2.9602", "0.8352", "0.8517", "0.8704", "0.9001", "0.5932"],
        ["No crop-stage features", "11 crop/stage/budget variables", "0.4620", "0.9807", "0.8806", "0.8914", "0.8850", "0.9250"],
        ["Soil-moisture threshold + mean dose", "Measured trigger only; no fertilizer", "4.7158", "0.1417", "-0.0695", "-0.0570", "-0.0320", "0.8194"],
    ]
    add_para(doc, "[Table 12 near here]")
    add_para(doc, "[Figure 8 near here]")

    doc.add_heading("3.12. Decision-threshold sensitivity and regression tests", level=2)
    add_para(doc, "At the deployed 0.5 m3 mu-1 threshold, the test set contained 2,194 true irrigation events, 4,514 true holds, 492 false triggers and no missed teacher events. Raising the model-output threshold to 2.0 m3 mu-1 reduced false triggers to 122 and increased specificity from 0.9017 to 0.9756 while retaining all 2,194 teacher events (Supplementary Table S4). The result reflects the discrete positive volumes produced by the generated teacher and should not be interpreted as proof of zero under-irrigation in the field. A field-calibrated threshold must instead balance crop stress risk against unnecessary actuation using measured soil trajectories and treatment outcomes.")
    add_para(doc, "After replacing legacy 20/40/60 cm test fixtures with the implemented single-probe interface, all 17 model/controller tests and all 18 server tests passed. The suites cover recommendation limits, channel-level closure, outlet interlocking, no-flow shutdown, weather summarization and critical-soil missing/stale gates. Passing tests verify deterministic software behavior for the encoded cases; they do not measure physical relay, pump or flow-meter reliability.")
    threshold_headers = ["Model threshold (m3 mu-1)", "TN", "FP", "FN", "TP", "Precision", "Recall", "F1", "Specificity"]
    threshold_rows = [
        ["0.1", "3,645", "1,361", "0", "2,194", "0.6172", "1.0000", "0.7633", "0.7281"],
        ["0.5", "4,514", "492", "0", "2,194", "0.8168", "1.0000", "0.8992", "0.9017"],
        ["1.0", "4,749", "257", "0", "2,194", "0.8951", "1.0000", "0.9447", "0.9487"],
        ["2.0", "4,884", "122", "0", "2,194", "0.9473", "1.0000", "0.9729", "0.9756"],
    ]
    add_para(doc, "[Table 13 near here]")

    doc.add_heading("3.13. Model iteration and learning-curve results", level=2)
    add_para(doc, "The 25-run learning curve shows that water error and nutrient reproduction improve as more training data are used, while classification performance approaches a plateau. At 2,880 training rows, mean irrigation decision accuracy was 0.8666 +/- 0.0037 and mean water MAE was 0.7593 +/- 0.0175 m3 mu-1. At 28,800 rows, these values were 0.9332 +/- 0.0044 and 0.3463 +/- 0.0167 m3 mu-1. Mean nutrient macro R2 increased from 0.8591 to 0.9649, and its between-seed standard deviation decreased from 0.0130 to 0.0012. Decision accuracy peaked at 0.9357 on average for the 75% subset and changed only slightly at full size, indicating a classification plateau rather than a strictly monotonic trend.")
    add_para(doc, "The 12-run validation parameter grid produced decision accuracy from 0.9278 to 0.9408, water R2 from 0.9883 to 0.9904 and nutrient macro R2 from 0.9561 to 0.9698. Increasing tree count above 200 yielded only small and non-monotonic changes. Minimum leaf size 4 consistently reduced nutrient macro R2, whereas leaf sizes 1 and 2 provided the strongest nutrient or decision metrics, respectively. The deployed 350-tree, leaf-size-2 configuration remains a defensible stability choice but is not claimed to be uniquely optimal. Full learning-curve, parameter-grid and run-level results are provided in Supplementary Tables S5-S7 and Supplementary Figure S3.")
    learning_summary_headers = ["Training fraction", "Rows", "Runs", "Decision accuracy", "Water MAE", "Water R2", "Nutrient macro R2"]
    learning_summary_rows = []
    for row in iteration_metrics["learning_curve_summary"]:
        learning_summary_rows.append([
            f'{float(row["fraction"]) * 100:.0f}%', f'{int(row["train_rows"]):,}', str(int(row["runs"])),
            f'{row["decision_mean"]:.4f} +/- {row["decision_sd"]:.4f}',
            f'{row["water_mae_mean"]:.4f} +/- {row["water_mae_sd"]:.4f}',
            f'{row["water_r2_mean"]:.4f} +/- {row["water_r2_sd"]:.4f}',
            f'{row["nutrient_r2_mean"]:.4f} +/- {row["nutrient_r2_sd"]:.4f}',
        ])
    parameter_headers = ["Trees", "Minimum leaf", "Water MAE", "Water R2", "Nutrient macro R2", "Decision accuracy"]
    parameter_rows = [[
        str(int(row["n_estimators"])), str(int(row["min_samples_leaf"])), f'{row["water_mae"]:.4f}',
        f'{row["water_r2"]:.4f}', f'{row["nutrient_macro_r2"]:.4f}', f'{row["decision_accuracy"]:.4f}',
    ] for row in iteration_metrics["parameter_sensitivity"]]
    iteration_headers = ["Training fraction", "Seed", "Rows", "Water MAE", "Water R2", "Nutrient macro R2", "Decision accuracy"]
    iteration_rows = [[
        f'{float(row["fraction"]) * 100:.0f}%', row["seed"], f'{int(row["train_rows"]):,}',
        f'{float(row["water_mae"]):.4f}', f'{float(row["water_r2"]):.4f}',
        f'{float(row["nutrient_macro_r2"]):.4f}', f'{float(row["decision_accuracy"]):.4f}',
    ] for row in iteration_raw]
    add_para(doc, "[Table 14 near here]")
    add_para(doc, "[Table 15 near here]")
    add_para(doc, "[Table 16 near here]")
    add_para(doc, "[Figure 9 near here]")

    doc.add_heading("4. Discussion", level=1)
    doc.add_heading("4.1. What is innovative in the present system", level=2)
    add_para(doc, "The main contribution is the integration boundary rather than a claim of a new agronomic optimum. The system explicitly assigns different responsibilities to the cloud and edge: the cloud can host a heavier policy model and weather processing, while the edge preserves local visibility and deterministic actuator protection. The single-probe assumption is also explicit. Instead of fabricating layered soil states, the decision path uses the measurements that the deployed hardware can actually provide. This makes the system easier to audit and calibrate in a small farm.")
    add_para(doc, "The second contribution is the combination of policy distillation and safety gates. A transparent teacher policy can be inspected by agronomists, while ExtraTrees provides a compact nonlinear approximation that is practical to serve. The model is never allowed to bypass hard gates. The third contribution is volume-based fertigation closure: relay state is not treated as delivered dose; flow pulses are used to terminate each nutrient channel, and the outlet pump is interlocked with channel completion. This directly addresses a failure mode that is common in timer-only prototypes.")
    add_para(doc, "The repeated training results strengthen the engineering evidence by showing how performance changes with training-set size and model settings. The reduction in average water error and nutrient-score dispersion supports use of the full chronological training pool, while the classification curve indicates diminishing returns after 75% of the training rows. The small differences among several tree counts and leaf sizes show that the reported result is not dependent on one isolated hyperparameter combination. This robustness concerns reproduction of the encoded teacher policy only.")

    doc.add_heading("4.2. Relation to prior work", level=2)
    add_para(doc, "The architecture follows the broader IoT agriculture direction in which distributed sensing, connectivity and analytics support site-specific management (Wolfert et al., 2017; Ayaz et al., 2019). It differs from many proof-of-concept systems by treating data quality and actuation sequencing as first-class control variables. The use of FAO-56 ET0 provides a recognized physical weather feature (Allen et al., 1998), but ET0 is used as context for a teacher policy and not as a substitute for local soil-water calibration. The ExtraTrees choice is consistent with its ability to model nonlinear interactions and mixed feature sets without requiring a large neural inference stack at the edge (Geurts et al., 2006). Recent irrigation-prediction research also demonstrates the feasibility of data-driven scheduling while emphasizing the need for site-specific validation (Jimenez et al., 2021).")

    doc.add_heading("4.3. Limitations and required next experiments", level=2)
    add_para(doc, "The most important limitation is label origin. The 39,600 samples are generated from regional historical weather, published crop-stage priors and single-probe setpoints. The model therefore reproduces a rule-based teacher; it has not learned a local yield response. The present evaluation also lacks randomized field treatments, calibrated flow-meter uncertainty, pump energy measurements, communication latency distributions and multi-season crop-quality labels. The project should next collect synchronized soil, flow, weather, management and harvest records; recalibrate each meter in situ; and compare the policy against timer irrigation and an agronomist baseline across independent plots. A future multi-objective model can then optimize yield, water productivity, nutrient use and energy under explicit constraints.")
    add_para(doc, "Operationally, the public-server dependency is a design trade-off. It simplifies model updates and allows a larger runtime environment, but it creates a need for a robust offline state and clear operator feedback. The current fail-closed soil gate and bounded relay state machine reduce physical risk, yet a production deployment should add local command expiry, signed firmware, authenticated transport, event persistence and an independent hardwired emergency stop.")
    doc.add_heading("4.4. Deployment implications", level=2)
    add_para(doc, "The design is appropriate for staged deployment. A first stage can use the server only for observation and work-order generation while an operator controls the relays manually. A second stage can enable automatic execution for irrigation with conservative limits and an active emergency stop. A third stage can enable closed-loop nutrient dosing after each flow meter has been calibrated and after the system has accumulated enough local observations to estimate delivery uncertainty. This staged approach is consistent with the evidence available in the current project: policy reproduction and actuator safety are demonstrated, while agronomic optimization is not yet demonstrated.")
    add_para(doc, "The same staged logic also improves scientific evaluation. During shadow mode, every proposed decision can be logged without changing the irrigation schedule. The log can then be paired with soil moisture trajectories, actual flow, weather, energy and harvest observations. Counterfactual comparisons between the teacher, ExtraTrees and the farmer baseline become possible only after these synchronized records are collected. In this sense, the current software is both a controller and an instrumentation platform for the next experiment.")
    doc.add_heading("4.5. Threats to validity", level=2)
    add_para(doc, "Three threats to validity should be considered. First, the weather data are regional and the soil scenarios are generated; they may not represent the microclimate, soil texture or hydraulic response of the eventual farm. Second, the model metrics are conditional on the teacher labels and can be inflated when train and test distributions share the same rule structure. Third, controller simulation assumes ideal pulse timing and does not measure pump wear, air entrainment, pressure variation or meter bias. These threats do not invalidate the engineering baseline, but they limit the strength of any agronomic claim. The manuscript intentionally reports them so that a subsequent field study can be designed around them.")

    doc.add_heading("5. Conclusions", level=1)
    add_para(doc, "This paper presented ZhiRun, a safety-gated edge-cloud fertigation system built for a single soil probe and four-pump hardware configuration. The system combines RS485 sensing, server-side ExtraTrees policy distillation, asymmetric missing-data handling, independent flow-meter closure for N/P/K dosing, and an outlet-pump interlock. On a chronological independent test set, the policy reproduction model achieved R2 values of 0.9805 for water, 0.9230 for N, 0.9254 for P2O5 and 0.9576 for K2O, with 93.17% irrigation decision accuracy. These metrics establish a reproducible software and control baseline. They do not establish agronomic or economic superiority. Local field trials with measured yield, quality, water and nutrient outcomes are required before the system can be promoted as an optimized automatic irrigation strategy.")

    doc.add_heading("References", level=1)
    refs = [
        "Allen RG, Pereira LS, Raes D, Smith M, 1998. Crop evapotranspiration: guidelines for computing crop water requirements. FAO Irrigation and Drainage Paper 56. Food and Agriculture Organization of the United Nations, Rome, Italy. Available from: https://www.fao.org/4/X0490E/X0490E00.htm",
        "Ayaz M, Ammad-Uddin M, Sharif Z, Mansour A, Aggoune EHM, 2019. Internet-of-Things (IoT)-based smart agriculture: toward making the fields talk. IEEE Access 7:129551-83. https://doi.org/10.1109/ACCESS.2019.2932609",
        "Benos L, Tagarakis AC, Dolias G, Berruto R, Kateris D, Bochtis D, 2021. Machine learning in agriculture: a comprehensive updated review. Sensors 21:3758. https://doi.org/10.3390/s21113758",
        "Breiman L, 2001. Random forests. Mach Learn 45:5-32. https://doi.org/10.1023/A:1010933404324",
        "Efron B, 1979. Bootstrap methods: another look at the jackknife. Ann Stat 7:1-26. https://doi.org/10.1214/aos/1176344552",
        "Fereres E, Soriano MA, 2007. Deficit irrigation for reducing agricultural water use. J Exp Bot 58:147-59. https://doi.org/10.1093/jxb/erl165",
        "Geurts P, Ernst D, Wehenkel L, 2006. Extremely randomized trees. Mach Learn 63:3-42. https://doi.org/10.1007/s10994-006-6226-1",
        "Jimenez AF, Ortiz BV, Bondesan L, Morata G, Damianidis D, 2021. Long short-term memory neural network for irrigation management: a case study from Southern Alabama, USA. Precis Agric 22:475-92. https://doi.org/10.1007/s11119-020-09753-z",
        "Jones HG, 2004. Irrigation scheduling: advantages and pitfalls of plant-based methods. J Exp Bot 55:2427-36. https://doi.org/10.1093/jxb/erh213",
        "Kamilaris A, Prenafeta-Boldu FX, 2018. Deep learning in agriculture: a survey. Comput Electron Agric 147:70-90. https://doi.org/10.1016/j.compag.2018.02.016",
        "Khanna A, Kaur S, 2019. Evolution of Internet of Things (IoT) and its significant impact in the field of precision agriculture. Comput Electron Agric 157:218-31. https://doi.org/10.1016/j.compag.2018.12.039",
        "Liakos KG, Busato P, Moshou D, Pearson S, Bochtis D, 2018. Machine learning in agriculture: a review. Sensors 18:2674. https://doi.org/10.3390/s18082674",
        "NASA Langley Research Center, 2024. NASA Prediction Of Worldwide Energy Resources (POWER) project documentation. Available from: https://power.larc.nasa.gov/docs/",
        "Pedregosa F, Varoquaux G, Gramfort A, Michel V, Thirion B, Grisel O, et al., 2011. Scikit-learn: machine learning in Python. J Mach Learn Res 12:2825-30. Available from: https://jmlr.org/papers/v12/pedregosa11a.html",
        "Saiz-Rubio V, Rovira-Mas F, 2020. From smart farming towards Agriculture 5.0: a review on crop data management. Agronomy 10:207. https://doi.org/10.3390/agronomy10020207",
        "Shi W, Cao J, Zhang Q, Li Y, Xu L, 2016. Edge computing: vision and challenges. IEEE Internet Things J 3:637-46. https://doi.org/10.1109/JIOT.2016.2579198",
        "Vellidis G, Tucker M, Perry C, Kvien C, Bednarz C, 2008. A real-time wireless smart sensor array for scheduling irrigation. Comput Electron Agric 61:44-50. https://doi.org/10.1016/j.compag.2007.05.009",
        "Wolfert S, Ge L, Verdouw C, Bogaardt MJ, 2017. Big data in smart farming: a review. Agric Syst 153:69-80. https://doi.org/10.1016/j.agsy.2017.01.023",
    ]
    for ref in refs: add_para(doc, ref)

    appendix_sections = [
        ("Appendix A. Runtime decision and execution algorithm", [
            "The following ordered procedure defines the boundary between model inference and physical execution.",
            "A1. Trigger. Start a decision cycle from the daily 12:00 scheduler or an authenticated manual request. Record trigger source, crop, date, area and current device identifier.",
            "A2. Snapshot validation. Reject automatic execution if the timestamp is stale or a critical soil field is missing or invalid.",
            "A3. Optional-context recovery. Substitute only current regional weather fallback values and mark their source.",
            "A4. Policy inference. Construct the validated feature vector, infer targets and apply all operational bounds.",
            "A5. Execution gate. Require an executable decision, an enabled execution path, an online edge device, a current command and no active fault.",
            "A6. Fertilizer dosing. Stop each N/P/K channel on its own incremental flow target; stop all outputs on any safety fault.",
            "A7. Outlet transfer. Release the outlet only after every requested fertilizer channel has completed and log the terminal state.",
        ]),
        ("Appendix B. Reproducibility manifest", [
            "B1. Configuration files define crop calendars, nutrient budgets, hardware mappings and sensor payloads.",
            "B2. NASA POWER weather data, source metadata, the training script, fitted model and metrics are retained in the repository.",
            "B3. Ablation, iteration, controller simulation, figure and document scripts rebuild the reported results.",
            "B4. Training uses 2015-2022, validation uses 2023 and independent testing uses 2024-2025. Production seed is 42; repeated runs use 11, 23, 37, 53 and 71; controller simulation uses 20260908.",
            "B5. Repository revision identifiers should be included in the final data-availability statement.",
        ]),
        ("Appendix C. Sensor and actuator semantics", [
            "C1. Soil moisture, pH and measured N/P/K are direct critical inputs from one soil probe; missing or invalid values block automatic execution.",
            "C2. Air and soil environment values are logged when available; tipping-bucket rain requires site calibration and debounce handling.",
            "C3. Forecast rain, radiation, ET0 and accumulated weather features are server-derived context, not local sensor readings.",
            "C4. IDLE, DOSING, OUTLET_TRANSFER, COMPLETE and FAULT/STOPPED define mutually constrained relay states.",
            "C5. Relay-on is an electrical command; delivered volume is incremental flow since the work-order baseline divided by calibrated pulses per litre.",
        ]),
    ]

    doc.add_heading("Tables", level=1)
    doc.add_paragraph("Table 1. Main hardware and software responsibilities.", style="CaptionText")
    add_table(doc, table1_headers, table1_rows)
    doc.add_paragraph("Table 2. Independent test performance for policy reproduction (2024-2025; n = 7,200).", style="CaptionText")
    add_table(doc, table2_headers, table2_rows)
    doc.add_paragraph("Table 3. Safety and interlock rules verified in the controller implementation.", style="CaptionText")
    add_table(doc, table3_headers, table3_rows)
    doc.add_paragraph("Table 4. Dataset composition, target sparsity and feature ranges.", style="CaptionText")
    add_table(doc, table4_headers, table4_rows)
    doc.add_paragraph("Table 5. Temporal and crop-level generalization results.", style="CaptionText")
    add_table(doc, table5_headers, table5_rows)
    doc.add_paragraph("Table 6. Comparison with transparent baselines on the independent test set.", style="CaptionText")
    add_table(doc, table6_headers, table6_rows)
    doc.add_paragraph("Table 7. Deterministic controller trace for an illustrative 0.01 mu work order.", style="CaptionText")
    add_table(doc, table7_headers, table7_rows)
    doc.add_paragraph("Table 8. Bootstrap 95% confidence intervals from 1,000 resamples of the 7,200-row independent test set.", style="CaptionText")
    add_table(doc, table8_headers, table8_rows)
    doc.add_paragraph("Table 9. Positive-event classification metrics on the independent test set.", style="CaptionText")
    add_table(doc, table9_headers, table9_rows)
    doc.add_heading("Figures", level=1)
    main_figures = [
        ("Figure 1. ZhiRun edge-cloud fertigation architecture. Boxes identify the field sensing, edge acquisition, server inference and ESP32-S3 actuation layers; arrows show the implemented communication paths.", fig_arch),
        ("Figure 2. Dataset composition and target sparsity. Panels show crop representation, positive target rates, the chronological split, and non-zero target magnitudes; n = 39,600.", fig_data),
        ("Figure 3. Independent test parity plots for water, N, P2O5 and K2O targets. Hexbin density is shown for n = 7,200 held-out rows; dashed lines are 1:1 references.", fig_parity),
        ("Figure 4. Temporal, crop-level and positive-event robustness. Results are computed from the stored model and held-out rows; no field-yield labels are used.", fig_gen),
        ("Figure 5. Deterministic controller timing trace. Fertilizer channels close on their own flow-meter targets before the outlet pump is released.", fig_ctrl),
        ("Figure 6. Bootstrap sampling uncertainty for independent-test MAE. Points are bootstrap means and bars are 95% percentile intervals from 1,000 resamples.", fig_unc),
        ("Figure 7. Controller Monte Carlo safety evaluation across 14,000 reproducible scenarios. Normal completion excludes the 80 deliberately bounded safety timeouts; every terminal state turned all outputs off.", fig_mc),
        ("Figure 8. Feature ablation and transparent-reference comparison. Bars show irrigation decision accuracy on the independent test years; labels are generated teacher-policy outcomes, not field treatment outcomes.", FIG_DIR / "fig8_ablation_comparison.png"),
        ("Figure 9. Repeated learning curves over five training fractions and five random seeds. Error bars show one standard deviation; all iteration evaluations use the fixed 2023 teacher-policy validation set.", fig_iterations),
    ]
    for caption, figure in main_figures[:6]:
        doc.add_paragraph(caption, style="CaptionText")
        doc.add_picture(str(figure), width=Inches(6.2))

    # The journal limits the combined number of tables and figures to 15.
    # Detailed robustness and iteration outputs are retained in a separate,
    # editable supplementary Word file for upload with the manuscript.
    supp = Document(); setup_document(supp)
    supp.add_heading("Supplementary Material", level=1)
    add_para(supp, "Supplementary results for the ZhiRun edge-cloud fertigation system. These tables and figures are excluded from the main-manuscript table/figure count and preserve the full robustness and iteration records.")
    for heading, paragraphs in appendix_sections:
        supp.add_heading(heading, level=1)
        for paragraph in paragraphs:
            add_para(supp, paragraph)
    for caption, headers, rows in [
        ("Supplementary Table S1. Optional-weather masking stress test on the independent test set.", table10_headers, table10_rows),
        ("Supplementary Table S2. Large-sample controller safety simulation using the production state machine.", table11_headers, table11_rows),
        ("Supplementary Table S3. Feature ablation and transparent soil-moisture reference on the independent test set.", ablation_headers, ablation_rows),
        ("Supplementary Table S4. Sensitivity of irrigation-event classification to the model-output threshold.", threshold_headers, threshold_rows),
        ("Supplementary Table S5. Repeated learning-curve results on the fixed 2023 validation set.", learning_summary_headers, learning_summary_rows),
        ("Supplementary Table S6. ExtraTrees parameter-sensitivity iterations.", parameter_headers, parameter_rows),
        ("Supplementary Table S7. Complete learning-curve iteration log for 25 model fits.", iteration_headers, iteration_rows),
    ]:
        supp.add_paragraph(caption, style="CaptionText")
        add_table(supp, headers, rows)
    supp.add_heading("Supplementary Figures", level=1)
    for caption, figure in main_figures[6:]:
        supp.add_paragraph(caption.replace("Figure 7", "Supplementary Figure S1").replace("Figure 8", "Supplementary Figure S2").replace("Figure 9", "Supplementary Figure S3"), style="CaptionText")
        supp.add_picture(str(figure), width=Inches(6.2))
    supp.core_properties.title = "ZhiRun supplementary material"
    supp.core_properties.author = "Li Tianhao; Liu Jiangping"
    supp.save(OUT_SUPP)

    # Remove placement markers and draft-only authoring notes from the clean manuscript copy.
    for paragraph in list(doc.paragraphs):
        if paragraph.text.strip().startswith("[") and paragraph.text.strip().endswith("near here]"):
            paragraph._element.getparent().remove(paragraph._element)
        if paragraph.text.strip().startswith("Draft for author completion:"):
            paragraph._element.getparent().remove(paragraph._element)
        if paragraph.style.name.startswith("Heading") and re.match(r"^\d+(?:\.\d+)*\.\s+", paragraph.text.strip()):
            paragraph.text = re.sub(r"^\d+(?:\.\d+)*\.\s+", "", paragraph.text.strip())

    doc.core_properties.title = "ZhiRun safety-gated edge-cloud fertigation system"
    doc.core_properties.subject = "Original research article draft for Journal of Agricultural Engineering"
    doc.core_properties.author = "Li Tianhao; Liu Jiangping"
    doc.core_properties.keywords = "fertigation; edge computing; ExtraTrees; flow-meter feedback; safety gate"
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build()
