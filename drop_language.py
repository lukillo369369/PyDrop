"""Drop: a small custom-language frontend for pydrop's existing node compiler.

Edit LANGUAGE_NAME, KEYWORDS, and WORD_OPERATORS to start customizing the syntax.
Quoted strings are tokenized separately; their contents are never rewritten.
"""
import ast
import io
import tokenize

from graph_core import GraphError, identifier
from code_to_nodes import translate_code

LANGUAGE_NAME = "Drop"
KEYWORDS = {"define": "let", "bind": "be", "input": "ask", "destination": "into", "output": "say"}
WORD_OPERATORS = {
    ("greater", "than", "or", "equal", "to"): ">=",
    ("less", "than", "or", "equal", "to"): "<=",
    ("not", "equal", "to"): "!=",
    ("greater", "than"): ">", ("less", "than"): "<", ("equal", "to"): "==",
    ("divided", "by"): "/", ("power", "of"): "**",
    ("plus",): "+", ("minus",): "-", ("times",): "*", ("modulo",): "%",
    ("and",): "and", ("or",): "or", ("not",): "not",
}
SAMPLE = '''let price be 12.5
let quantity be 3
let total be price times quantity
ask "Your name? " into name
say "Hello " plus name
say total'''


def to_python(source):
    """Convert one Drop statement per line, preserving diagnostic line numbers."""
    if not isinstance(source,str) or not source.strip() or len(source) > 12000:
        raise GraphError(f"Enter a {LANGUAGE_NAME} script of up to 12,000 characters.")
    phrases = sorted(WORD_OPERATORS,key=len,reverse=True)
    reserved = set(KEYWORDS.values()) | {word for phrase in phrases for word in phrase} | {"true","false"}

    def name(token, line):
        if token.type != tokenize.NAME or token.string.lower() in reserved:
            raise GraphError(f"Line {line}: choose a name that is not a {LANGUAGE_NAME} keyword.")
        try:
            return identifier(token.string)
        except GraphError as exc:
            raise GraphError(f"Line {line}: {exc}") from None

    def expression(tokens, line):
        if not tokens:
            raise GraphError(f"Line {line}: expected a value or expression.")
        result = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type == tokenize.NAME:
                for phrase in phrases:
                    group = tokens[index:index+len(phrase)]
                    if len(group) == len(phrase) and all(t.type == tokenize.NAME and t.string.lower() == word for t,word in zip(group,phrase)):
                        result.append(WORD_OPERATORS[phrase])
                        index += len(phrase)
                        break
                else:
                    result.append({"true":"True","false":"False"}.get(token.string.lower(),token.string))
                    index += 1
                continue
            if token.type not in (tokenize.NUMBER,tokenize.STRING,tokenize.OP):
                raise GraphError(f"Line {line}: unsupported character or expression.")
            if token.string == ";":
                raise GraphError(f"Line {line}: put each statement on a separate line.")
            result.append(token.string)
            index += 1
        return " ".join(result)

    converted = []
    statements = 0
    for line, text in enumerate(source.splitlines(),1):
        try:
            tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)
                      if t.type not in (tokenize.ENCODING,tokenize.ENDMARKER,tokenize.NEWLINE,
                                        tokenize.NL,tokenize.INDENT,tokenize.DEDENT,tokenize.COMMENT)]
        except (tokenize.TokenError,IndentationError,SyntaxError):
            raise GraphError(f"Line {line}: finish the expression and close its quotes or parentheses.") from None
        if not tokens:
            converted.append("#")
            continue
        statements += 1
        command = tokens[0].string.lower()
        if command == KEYWORDS["define"]:
            if len(tokens) < 4 or tokens[2].string.lower() != KEYWORDS["bind"]:
                raise GraphError(f"Line {line}: expected {KEYWORDS['define']} name {KEYWORDS['bind']} expression.")
            target = name(tokens[1],line)
            converted.append(f"{target} = {expression(tokens[3:],line)}")
        elif command == KEYWORDS["input"]:
            if len(tokens) < 4 or tokens[-2].string.lower() != KEYWORDS["destination"]:
                raise GraphError(f"Line {line}: expected {KEYWORDS['input']} \"question\" {KEYWORDS['destination']} name.")
            target = name(tokens[-1],line)
            prompt = tokens[1:-2]
            if not prompt or any(t.type != tokenize.STRING for t in prompt):
                raise GraphError(f"Line {line}: the question must be quoted text.")
            converted.append(f"{target} = input({expression(prompt,line)})")
        elif command == KEYWORDS["output"]:
            converted.append(f"print({expression(tokens[1:],line)})")
        else:
            raise GraphError(f"Line {line}: unknown statement '{tokens[0].string}'. Use {KEYWORDS['define']}, {KEYWORDS['input']}, or {KEYWORDS['output']}.")
        try:
            ast.parse(converted[-1],mode="exec")
        except (SyntaxError,ValueError,RecursionError):
            raise GraphError(f"Line {line}: invalid or incomplete expression. Check operators, parentheses, and quoted text.") from None
    if not statements:
        raise GraphError(f"Enter a {LANGUAGE_NAME} statement, such as {KEYWORDS['output']} \"Hello\".")
    return "\n".join(converted)


def translate_drop(source, original, x, y):
    """Translate atomically through the same node model, layout, and validation."""
    return translate_code(to_python(source),original,x,y)
