; Racket/Scheme symbol extraction

(list
  (symbol) @_keyword
  (symbol) @name.definition.function
  (#match? @_keyword "^(define|define/contract|define/public|define/private|define/override)$")) @definition.function

(list
  (symbol) @_keyword
  (list
    (symbol) @name.definition.function)
  (#match? @_keyword "^(define|define/contract|define/public|define/private|define/override)$")) @definition.function

(list
  (symbol) @_keyword
  (symbol) @name.definition.class
  (#match? @_keyword "^(struct|class|interface|define-struct)$")) @definition.class

(list
  (symbol) @_keyword
  (symbol) @name.reference.import
  (#match? @_keyword "^(require|provide)$")) @reference.import

(list
  (symbol) @name.reference.call) @reference.call

(application
  (symbol) @name.reference.call) @reference.call
