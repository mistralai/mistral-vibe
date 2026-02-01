; Clojure symbol extraction

(list_lit
  value: (sym_lit) @_keyword
  value: (sym_lit) @name.definition.function
  (#match? @_keyword "^(defn|defn-|defmacro|defmethod|defmulti)$")) @definition.function

(list_lit
  value: (sym_lit) @_keyword
  value: (sym_lit) @name.definition.class
  (#match? @_keyword "^(defprotocol|defrecord|deftype|definterface)$")) @definition.class

(list_lit
  value: (sym_lit) @_keyword
  value: (sym_lit) @name.definition.property
  (#match? @_keyword "^(def|defonce)$")) @definition.property

(list_lit
  value: (sym_lit) @_keyword
  value: (sym_lit) @name.reference.import
  (#match? @_keyword "^(require|use|import)$")) @reference.import

(list_lit
  value: (sym_lit) @name.reference.call) @reference.call

(anon_fn_lit
  value: (sym_lit) @name.reference.call) @reference.call
