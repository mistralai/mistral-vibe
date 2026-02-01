; Nix symbol extraction

(binding
  attrpath: (attrpath
    attr: (identifier) @name.definition.property)) @definition.property

(function_expression
  universal: (identifier) @name.definition.function) @definition.function

(attrset_expression
  (binding
    attrpath: (attrpath
      attr: (identifier) @name.definition.property))) @definition.property

(apply_expression
  function: (variable_expression
    name: (identifier) @name.reference.call)) @reference.call

(apply_expression
  function: (select_expression
    attrpath: (attrpath
      attr: (identifier) @name.reference.call))) @reference.call

(inherit
  (inherited_attrs
    attr: (identifier) @name.reference.import)) @reference.import

(path_expression
  (path_fragment) @name.reference.import) @reference.import
