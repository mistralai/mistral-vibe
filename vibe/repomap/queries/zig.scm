; Zig symbol extraction

(FnProto
  name: (IDENTIFIER) @name.definition.function) @definition.function

(VarDecl
  name: (IDENTIFIER) @name.definition.property) @definition.property

(ContainerDecl
  (IDENTIFIER) @name.definition.class) @definition.class

(TestDecl
  (STRINGLITERALSINGLE) @name.definition.function) @definition.function

(FieldAccess
  field: (IDENTIFIER) @name.reference.call) @reference.call

(FnCallExpr
  (IDENTIFIER) @name.reference.call) @reference.call

(FnCallExpr
  (BuiltinCallExpr
    function: (BUILTINIDENTIFIER) @name.reference.call)) @reference.call

(SuffixExpr
  (IDENTIFIER) @name.reference.call) @reference.call
