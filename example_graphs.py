"""Small, editable pydrop lessons, ordered from text to decision making."""
from graph_core import Graph, branch_demo, demo_graph


def greeting_demo():
    g = Graph()
    main = g.add_node("Main", 40, 50)
    message = g.add_node("Variable", 40, 240)
    message.props.update(name="greeting", type="Text", value="Hello from pydrop!")
    out = g.add_node("Print", 370, 50)
    out.props.update(label="")
    g.connect(main.id, "next", out.id, "run")
    g.connect(message.id, "value", out.id, "value")
    return g


def formula_demo(name, expression, inputs, label):
    g = Graph()
    main = g.add_node("Main", 40, 40)
    fn = g.add_node("Function", 370, 40)
    fn.props.update(name=name, expression=expression)
    out = g.add_node("Print", 700, 40)
    out.props.update(label=label)
    g.connect(main.id, "next", fn.id, "run")
    g.connect(fn.id, "next", out.id, "run")
    g.connect(fn.id, "result", out.id, "value")
    for index, (port, variable_name, value) in enumerate(inputs):
        variable = g.add_node("Variable", 40, 210 + index * 155)
        variable.props.update(name=variable_name, value=value)
        g.connect(variable.id, "value", fn.id, port)
    return g


def tip_demo():
    return formula_demo("split_bill", "round(a * (1 + b / 100) / c, 2)",
                        [("a", "bill_amount", "80"), ("b", "tip_percent", "20"),
                         ("c", "people", "4")], "Each person pays: $")


def conversion_demo():
    return formula_demo("to_fahrenheit", "round(a * 9 / 5 + 32, 1)",
                        [("a", "celsius", "25")], "Fahrenheit: ")


def triangle_demo():
    return formula_demo("hypotenuse", "round(sqrt(a * a + b * b), 3)",
                        [("a", "side_a", "3"), ("b", "side_b", "4")], "Hypotenuse: ")


def area_demo():
    g = Graph()
    main = g.add_node("Main", 40, 40)
    width = g.add_node("Variable", 40, 210)
    width.props.update(name="width", value="8")
    height = g.add_node("Variable", 40, 370)
    height.props.update(name="height", value="5")
    area = g.add_node("Math", 360, 210)
    area.props.update(operator="*")
    out = g.add_node("Print", 680, 40)
    out.props.update(label="Rectangle area: ")
    g.connect(main.id, "next", out.id, "run")
    g.connect(width.id, "value", area.id, "a")
    g.connect(height.id, "value", area.id, "b")
    g.connect(area.id, "result", out.id, "value")
    return g


def pass_demo():
    g = branch_demo()
    g.nodes["n2"].props.update(name="score", value="72")
    g.nodes["n3"].props.update(b="60")
    g.nodes["n5"].props.update(value="Passed! Score is at least 60.")
    g.nodes["n6"].props.update(value="Keep practicing. Score is below 60.")
    return g


def interactive_calculator_demo():
    g = Graph()
    main = g.add_node("Main", 40, 40)
    first = g.add_node("Input", 350, 40)
    first.props.update(prompt="First number: ")
    second = g.add_node("Input", 660, 40)
    second.props.update(prompt="Second number: ")
    a = g.add_node("Convert", 350, 260)
    b = g.add_node("Convert", 660, 260)
    total = g.add_node("Math", 660, 460)
    message = g.add_node("Text Join", 970, 260)
    message.props.update(a="Sum: ", separator="")
    out = g.add_node("Print", 970, 40)
    out.props.update(label="")
    g.connect(main.id,"next",first.id,"run")
    g.connect(first.id,"next",second.id,"run")
    g.connect(second.id,"next",out.id,"run")
    g.connect(first.id,"result",a.id,"value")
    g.connect(second.id,"result",b.id,"value")
    g.connect(a.id,"result",total.id,"a")
    g.connect(b.id,"result",total.id,"b")
    g.connect(total.id,"result",message.id,"b")
    g.connect(message.id,"result",out.id,"value")
    return g


def controlled_temperature_demo():
    g = branch_demo()
    enabled = g.add_node("Variable",330,470)
    enabled.props.update(name="system_enabled",type="Boolean",value="True")
    logic = g.add_node("Boolean Logic",630,260)
    g.nodes["n4"].x = 940
    g.nodes["n5"].x = g.nodes["n6"].x = 1250
    g.nodes["n5"].props.update(value="Fan ON (hot and enabled)")
    g.nodes["n6"].props.update(value="Fan OFF")
    g.connect("n3","result",logic.id,"a")
    g.connect(enabled.id,"value",logic.id,"b")
    g.connect(logic.id,"result","n4","condition")
    return g


def clamp_demo():
    g = Graph()
    main = g.add_node("Main",40,40)
    request = g.add_node("Variable",40,230)
    request.props.update(name="requested_power",value="125")
    clamp = g.add_node("Clamp",350,230)
    out = g.add_node("Print",660,40)
    out.props.update(label="Clamped power: ")
    g.connect(main.id,"next",out.id,"run")
    g.connect(request.id,"value",clamp.id,"value")
    g.connect(clamp.id,"result",out.id,"value")
    return g


def dice_demo():
    g = Graph()
    main = g.add_node("Main",40,40)
    dice = g.add_node("Random",350,40)
    dice.props.update(seed="42")
    out = g.add_node("Print",660,40)
    out.props.update(label="Dice roll: ")
    g.connect(main.id,"next",dice.id,"run")
    g.connect(dice.id,"next",out.id,"run")
    g.connect(dice.id,"result",out.id,"value")
    return g


def drop_example(filename):
    """Build the menu graph from the same editable Drop source users can read."""
    from pathlib import Path
    from drop_language import translate_drop
    from code_to_nodes import connect_drop
    source = (Path(__file__).resolve().parent / "examples" / filename).read_text(encoding="utf-8")
    return connect_drop(translate_drop(source,Graph(),40,40),"After Main").graph


def drop_shopping_demo():
    return drop_example("shopping.drop")


def drop_logic_demo():
    return drop_example("logic.drop")


def reusable_rectangle_demo():
    """Two calls share one editable graph with two named results."""
    graph = Graph()
    definition = graph.add_function("rectangle_metrics", ["width", "height"], ["area", "perimeter"])
    body = Graph.from_dict(definition["graph"])
    area = body.add_node("Math", 360, 210)
    area.props["operator"] = "*"
    sides = body.add_node("Math", 360, 450)
    perimeter = body.add_node("Math", 700, 450)
    perimeter.props.update(operator="*", b="2")
    body.nodes["n3"].x = 1040
    for node in (area, sides):
        body.connect("n2", "width", node.id, "a")
        body.connect("n2", "height", node.id, "b")
    body.connect(sides.id, "result", perimeter.id, "a")
    body.connect(area.id, "result", "n3", "area")
    body.connect(perimeter.id, "result", "n3", "perimeter")
    definition["graph"] = body.to_dict()
    main = graph.add_node("Main", 30, 40)
    previous = main
    for index, (width, height, label) in enumerate((("2", "3", "First"), ("4", "5", "Second"))):
        y = 40 + index*340
        call = graph.add_call("rectangle_metrics", 360, y)
        call.props["inputs"][0]["default"] = width
        call.props["inputs"][1]["default"] = height
        graph.connect(previous.id, "next", call.id, "run")
        previous = call
        for column, key in enumerate(("area", "perimeter")):
            output = graph.add_node("Print", 710+column*330, y)
            output.props["label"] = f"{label} {key}: "
            graph.connect(previous.id, "next", output.id, "run")
            graph.connect(call.id, key, output.id, "value")
            previous = output
    return graph


def engineering_demo(kind, values, target_unit, label):
    """Quantities feed a typed engineering calculation and explicit conversion."""
    from engineering_units import ENGINEERING
    graph = Graph()
    main = graph.add_node("Main", 40, 40)
    calculation = graph.add_node(kind, 380, 210)
    for index, (pin, magnitude, unit) in enumerate(values):
        quantity = graph.add_node("Quantity", 40, 210 + index * 220)
        quantity.props.update(value=magnitude, unit=unit)
        graph.connect(quantity.id, "result", calculation.id, pin)
    converted = graph.add_node("Unit Convert", 720, 210)
    converted.props.update(from_unit=ENGINEERING[kind][1], unit=target_unit)
    graph.connect(calculation.id, "result", converted.id, "value")
    output = graph.add_node("Print", 1060, 40)
    output.props["label"] = label
    graph.connect(main.id, "next", output.id, "run")
    graph.connect(converted.id, "result", output.id, "value")
    return graph


def engineering_force_demo():
    return engineering_demo("Force", [("mass", "2500", "g"), ("acceleration", "9.81", "m/s^2")], "N", "Force: ")


def torque_demo():
    return engineering_demo("Torque", [("force", "100", "N"), ("distance", "250", "mm")], "N*m", "Torque: ")


def ohms_law_demo():
    return engineering_demo("Ohm's Law", [("current", "250", "mA"), ("resistance", "2", "kohm")], "V", "Voltage: ")


def electrical_power_demo():
    return engineering_demo("Electrical Power", [("voltage", "12", "V"), ("current", "500", "mA")], "W", "Power: ")


def experimental_measurements_demo(data_path=None):
    """Measured voltage/current -> elementwise power -> table, plots, CSV."""
    from pathlib import Path
    graph = Graph()
    main = graph.add_node("Main", 40, 40)
    source = graph.add_node("CSV Input", 350, 40)
    source.props.update(path=str(data_path or Path(__file__).resolve().parent / "examples" / "experimental_measurements.csv"),
                        x_column="voltage_V", y_column="current_A")
    power = graph.add_node("Math", 350, 300)
    power.props["operator"] = "*"
    table = graph.add_node("Data Table", 690, 40)
    table.props["column_name"] = "power_W"
    line = graph.add_node("Line Plot", 1030, 40)
    line.props.update(x_column="time_s", y_column="power_W", title="Experimental electrical power",
                      x_label="Time (s)", y_label="Power (W)")
    scatter = graph.add_node("Scatter Plot", 1370, 40)
    scatter.props.update(x_column="voltage_V", y_column="current_A", title="Measured current versus voltage",
                         x_label="Voltage (V)", y_label="Current (A)")
    output = graph.add_node("CSV Output", 1710, 40)
    output.props.update(path="experimental_results.csv", mode="Overwrite")
    message = graph.add_node("Print", 2050, 40)
    message.props.update(label="", value="Calculated power_W = voltage_V * current_A; wrote experimental_results.csv")
    actions = [main, source, table, line, scatter, output, message]
    for previous, following in zip(actions, actions[1:]):
        graph.connect(previous.id, "next", following.id, "run")
    graph.connect(source.id, "x", power.id, "a")
    graph.connect(source.id, "y", power.id, "b")
    graph.connect(source.id, "data", table.id, "data")
    graph.connect(power.id, "result", table.id, "values")
    for node in (line, scatter, output):
        graph.connect(table.id, "data", node.id, "data")
    return graph


def sensor_dashboard_demo():
    """Request one simulated Arduino sample and display its two numeric fields."""
    graph = Graph()
    main = graph.add_node("Main", 40, 40)
    connection = graph.add_node("Serial Connect", 360, 40)
    connection.props.update(port="SIMULATED", baud="9600", timeout="1", mode="Simulation")
    connection_status = graph.add_node("Print", 680, 40)
    connection_status.props["label"] = "Connection: "
    send = graph.add_node("Serial Send", 1000, 40)
    send.props.update(command="READ", newline="LF")
    read = graph.add_node("Serial Read Line", 1320, 40)
    read.props["timeout"] = "1"
    received = graph.add_node("Branch", 1640, 40)
    temperature = graph.add_node("Parse Value", 1640, 290)
    temperature.props.update(key="temperature", separator=",")
    light = graph.add_node("Parse Value", 1640, 510)
    light.props.update(key="light", separator=",")
    temperature_output = graph.add_node("Print", 1960, 40)
    temperature_output.props["label"] = "Temperature (C): "
    light_output = graph.add_node("Print", 2280, 40)
    light_output.props["label"] = "Light (ADC): "
    disconnect = graph.add_node("Serial Disconnect", 2600, 40)
    disconnected_status = graph.add_node("Print", 2920, 40)
    disconnected_status.props["label"] = "Connection: "
    # An empty read is a normal timeout. Avoid parsing it and still close the port.
    no_sample = graph.add_node("Print", 1960, 330)
    no_sample.props["label"] = "Sensor read: "
    timeout_disconnect = graph.add_node("Serial Disconnect", 2280, 330)
    timeout_status = graph.add_node("Print", 2600, 330)
    timeout_status.props["label"] = "Connection: "
    actions = (main, connection, connection_status, send, read, received)
    for previous, following in zip(actions, actions[1:]):
        graph.connect(previous.id, "next", following.id, "run")
    graph.connect(connection.id, "status", connection_status.id, "value")
    graph.connect(read.id, "received", received.id, "condition")
    graph.connect(received.id, "true", temperature_output.id, "run")
    for previous, following in zip((temperature_output, light_output, disconnect),
                                   (light_output, disconnect, disconnected_status)):
        graph.connect(previous.id, "next", following.id, "run")
    for parser, output in ((temperature, temperature_output), (light, light_output)):
        graph.connect(read.id, "line", parser.id, "line")
        graph.connect(parser.id, "result", output.id, "value")
    graph.connect(disconnect.id, "status", disconnected_status.id, "value")
    graph.connect(received.id, "false", no_sample.id, "run")
    graph.connect(read.id, "status", no_sample.id, "value")
    graph.connect(no_sample.id, "next", timeout_disconnect.id, "run")
    graph.connect(timeout_disconnect.id, "next", timeout_status.id, "run")
    graph.connect(timeout_disconnect.id, "status", timeout_status.id, "value")
    return graph


# Slugs also name the distributable editable graph files.
EXAMPLES = (
    ("sensor_dashboard", "Arduino | Simulated sensor dashboard", sensor_dashboard_demo),
    ("experimental_measurements", "Experimental measurements | CSV, power, tables and plots", experimental_measurements_demo),
    ("engineering_force", "Engineering | Force with units", engineering_force_demo),
    ("torque", "Engineering | Torque with units", torque_demo),
    ("ohms_law", "Engineering | Ohm's Law with units", ohms_law_demo),
    ("electrical_power", "Engineering | Electrical Power with units", electrical_power_demo),
    ("reusable_rectangle", "Reusable function | Two rectangles", reusable_rectangle_demo),
    ("drop_shopping", "Drop language | Shopping and input", drop_shopping_demo),
    ("drop_logic", "Drop language | Boolean decisions", drop_logic_demo),
    ("controlled_temperature", "Temperature controller | Boolean logic", controlled_temperature_demo),
    ("clamp_power", "Limit power | Clamp", clamp_demo),
    ("dice", "Dice roller | Seeded random", dice_demo),
    ("interactive_calculator", "Interactive calculator | Input, convert, join", interactive_calculator_demo),
    ("greeting", "Hello pydrop | Text and output", greeting_demo),
    ("rectangle_area", "Rectangle area | Math nodes", area_demo),
    ("temperature_conversion", "Celsius to Fahrenheit | Formulas", conversion_demo),
    ("triangle", "Right triangle | Math functions", triangle_demo),
    ("split_bill", "Split a bill | Three inputs", tip_demo),
    ("pass_or_fail", "Pass or fail | Decisions", pass_demo),
    ("force", "Force calculation | Physics", demo_graph),
    ("temperature", "Temperature switch | Branches", branch_demo),
)


if __name__ == "__main__":
    from pathlib import Path
    destination = Path(__file__).resolve().parent / "examples"
    destination.mkdir(exist_ok=True)
    for slug, title, factory in EXAMPLES:
        graph = factory("experimental_measurements.csv") if slug == "experimental_measurements" else factory()
        graph.save(destination / (slug + ".nodeforge.json"))
    print(f"Saved {len(EXAMPLES)} example graphs to {destination}")
